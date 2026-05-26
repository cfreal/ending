"""Provides validation tools for designs, methods, and queries."""

from __future__ import annotations

import random
import re
from abc import ABC, abstractmethod
from typing import Any, Callable

from ending.ast import (
    Alias,
    BoolType,
    Concatenation,
    Count,
    IntType,
    Node,
    Ord,
    Query,
    Star,
    Substring,
    TextType,
    Value,
)
from ending.cli.design import Design
from ending.db.generic.compiler import Compiler
from ending.db.generic.map import Mapper
from ending.db.generic.method import ChunkMethod, Method, SelectMethod
from ending.struct.livestatus import LiveStatus, VoidLiveStatus
from ending.util import randomized

__all__ = [
    "Validator",
    "MethodValidator",
    "DisplayMethodValidator",
    "SelectMethodValidator",
    "ChunkMethodValidator",
    "TypedQueriesValidator",
    "DesignValidator",
    "DesignSetupValidator",
    "DesignTeardownValidator",
    "DesignSetConfigurationValidator",
    "QueryValidator",
    "ValidationError",
]


# List of solutions to common problems

SOLUTION_SORRY = "You're on your own, sorry"
SOLUTION_HEXADECIMAL = "Convert columns to hexadecimal (`hex=True`)"


def output_merge(values: list[str]) -> str:
    return ", ".join(f"`{v}`" for v in values)


class ValidationError(Exception):
    """Raised when a validation fails."""

    problems: dict[str, list[str]]

    def add_problem_solutions(self, problem: str, *solutions: str) -> None:
        self.problems.setdefault(problem, []).extend(solutions)

    def __init__(self, problem: str = None, *solutions: list[str]) -> None:
        self.problems = {}
        if problem:
            self.add_problem_solutions(problem, *solutions)

    def __str__(self):
        return "\n".join(self.problems)

    def __add__(self, other: ValidationError) -> ValidationError:
        new = ValidationError()
        for problem, solutions in self.problems.items():
            new.add_problem_solutions(problem, *solutions)
        for problem, solutions in other.problems.items():
            new.add_problem_solutions(problem, *solutions)
        return new


class Validator(ABC):
    """An object that validates (i.e. verifies) that a part of the design is working properly.
    It is used to validate methods, designs, and queries.
    It produces messages using a `LiveStatus` object, and raises a `ValidationError` in case of an error.
    """

    name: str
    """Name of the validation process."""
    status: LiveStatus

    def __init__(self, status: LiveStatus = None):
        self.status = status or VoidLiveStatus()

    @abstractmethod
    async def validate(self) -> None:
        """Validates that the object is working as expected.

        Messages indicating the progress get displayed. In case of a problem, a
        `ValidationError` is raised.
        """
        self.status.section(self.name)


class MethodValidator(Validator, ABC):
    """Verifies that injection methods are working as expected."""

    name: str = "Method"
    method: Method
    """Injection method to test"""

    def __init__(self, method: Method, status: LiveStatus = None) -> None:
        super().__init__(status)
        self.method = method

    @abstractmethod
    async def validate(self) -> None:
        """Verifies that the method is working as expected.
        In case of a problem, a ValidationError is raised.
        Otherwise, while running, the method yields strings indicating parts of
        the method that are deemed to behave correctly.
        """
        # The strategy is to sollicitate small parts of the method and check if
        # they behave properly on the target
        await super().validate()

    def _solution_params(self, *params: str) -> str:
        return f"Check the {output_merge(params)} parameter(s) of the method"


class DisplayMethodValidator(MethodValidator, ABC):
    """Validates methods that display the results of the query in the response."""

    MARKER_START = "NenuPhaR"
    MARKER_END = "JacK"
    MARKER_SEP = "ouille"
    PATTERN = re.compile(
        rf"({MARKER_START})(.*?)({MARKER_END})".encode(),
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _get_test_items(self) -> dict[str, Callable]:
        return {
            "<": self._validate_less_than,
            "'": self._validate_quote,
            "\n": self._validate_newline,
        }

    async def _test_payload(self, payload: Node) -> list[bytes]:
        payload = f"{self.MARKER_START}{payload}{self.MARKER_END}"
        payload = self.method._maybe_split_tag(payload)
        payload = len(payload) == 1 and payload[0] or Concatenation(payload)
        response = await self.inject_raw(payload)
        results = self.PATTERN.findall(response)
        return results

    async def _validate_results_are_present_and_properly_cased(
        self, results: list[bytes]
    ) -> None:
        # Check number of results
        if len(results) == 1:
            self.status.success("The results are reflected **once** in the page")
        elif not results:
            raise ValidationError(
                "The results are **not reflected** in the page",
                "Check that the method parameters are correct",
            )
        # More than 1
        else:
            self.status.warning(
                f"The results are reflected **{len(results)} times** in the page",
            )

        # Check case

        r_marker_start, data, _ = results[0]
        e_marker_start = self.MARKER_START.encode()

        if r_marker_start == e_marker_start:
            self.status.success("The results' case is **not** modified")
        elif r_marker_start == e_marker_start.lower():
            raise ValidationError(
                f"The results are displayed as **lowercase**",
                SOLUTION_HEXADECIMAL,
            )
        elif r_marker_start == e_marker_start.upper():
            raise ValidationError(
                f"The results are displayed as **uppercase**",
                SOLUTION_HEXADECIMAL,
            )

    def _md_repr(self, value: str | bytes) -> str:
        if isinstance(value, str):
            left = 1
        elif isinstance(value, bytes):
            left = 2
        else:
            raise TypeError()
        return f"`{repr(value)[left:-1]}`"

    def _raise_removed_or_unknown(self, item: str, value: bytes) -> None:
        if value == b"":
            raise ValidationError(
                f"{self._md_repr(item)} are **removed** from the response",
                SOLUTION_HEXADECIMAL,
            )
        raise ValidationError(
            f"The test character {self._md_repr(item)} is reflected as {self._md_repr(value)}",
            SOLUTION_SORRY,
        )

    async def _validate_quote(self, value: bytes) -> None:
        match value:
            case b"'":
                self.status.success("Single quotes are **not** escaped or removed")
            case b"\\'":
                raise ValidationError(
                    "Single quotes are **escaped** using a **backslash** (`\\'`)",
                    "Remove quote escaping from the response",
                )
            case b"''":
                raise ValidationError(
                    "Single quotes are **escaped** using another **single quote** (`''`)",
                    "Remove quote escaping from the response",
                )
            case _:
                self._raise_removed_or_unknown("'", value)

    async def _validate_less_than(self, value: bytes) -> None:
        match value.lower():
            case b"<":
                self.status.success("HTML characters are **not** escaped or removed")
            case b"&lt;":
                raise ValidationError(
                    "The results are **HTML-escaped**",
                    SOLUTION_HEXADECIMAL,
                    "Use `html.unescape()` on the response",
                )
            case _:
                self._raise_removed_or_unknown("<", value)

    async def _validate_newline(self, value: bytes) -> None:
        match value:
            case b"\n":
                self.status.success("Newlines are **not** escaped or removed")
            case b" ":
                raise ValidationError(
                    f"Newlines (`'\\n'`) are **converted** to spaces (`' '`)",
                    SOLUTION_HEXADECIMAL,
                )
            case _:
                self._raise_removed_or_unknown("\n", value)

    async def validate(self) -> None:
        """Validates that the method properly works when displaying strange characters,
        such as `<`, `'`, and `\\n`.
        """
        await super().validate()
        test_items = self._get_test_items()

        # Let's try to fetch every test item at once. If it works, we're good to go
        # already and can go to the next step

        payload = self.MARKER_SEP.join(test_items)
        results = await self._test_payload(payload)

        # First, let's find out if we can see something

        # No results: either the method does not work, or we have some kind denylist
        if not results:
            # Let's fetch a simple payload with no potential badchars to check which
            # of the above case we are in

            results = await self._test_payload("")
            await self._validate_results_are_present_and_properly_cased(results)

            # We have an escaping mechanism in place. Let's try to find out which one.

            item_results = [
                (item, await self._test_payload(item)) for item in test_items
            ]

            # Items that give no results are probably denylisted

            no_result_items = [item for item, results in item_results if not results]

            if no_result_items:
                items = ", ".join(self._md_repr(item) for item in no_result_items)
                solutions = [
                    "A denylist might be in place",
                    "Try setting [b]hex=True[/b], if available",
                ]
                if "'" in no_result_items and "'" in self.method.compiler.quote("'"):
                    solutions.append(
                        "Change the compiler's quote method to one that does not use single quotes"
                    )
                raise ValidationError(
                    f"Unable to get output when injecting these characters: {items}",
                    *solutions,
                )

            # This is really unlikely to happen, but let's check anyway

            for item, results in item_results:
                if len(results) > 1:
                    raise ValidationError(
                        f"Injecting {self._md_repr(item)} yields **{len(results)}** results instead of **1**",
                        SOLUTION_SORRY,
                        "This is very unlikely to happen",
                    )

            # We could not identify why the first request failed. Max length ?

            items = ", ".join(self._md_repr(item) for item in test_items)
            raise ValidationError(
                f"Although injecting {self._md_repr(item)} one-by-one works, injecting them all at once fails",
                SOLUTION_SORRY,
                "There might be a maximum length on the output",
            )

        error = ValidationError()

        try:
            await self._validate_results_are_present_and_properly_cased(results)
        except ValidationError as e:
            error += e

        data = results[0][1]

        # Check if characters that have high chances of getting modified are
        # indeed modified
        data = re.split(self.MARKER_SEP.encode(), data, flags=re.IGNORECASE)

        # We should have the same number of result items as the number of test items

        if len(data) != len(test_items):
            raise ValidationError(
                f"The data is **not** reflected as expected: `{data!r}` != `{list(test_items)!r}`",
                SOLUTION_SORRY,
            )

        # Run the custom validator for each item

        for (item, validator), result in zip(test_items.items(), data):
            try:
                await validator(result)
            except ValidationError as e:
                error += e

        if error.problems:
            raise error

    @abstractmethod
    async def inject_raw(self, payload: Node) -> bytes:
        """Injects the payload and returns the raw response."""


class SelectMethodValidator(DisplayMethodValidator):
    """Validation for `SelectMethod`."""

    method: SelectMethod

    async def inject_raw(self, payload: Node) -> bytes:
        columns = self.method.columns[:]
        columns[self.method.column] = payload
        query = Query().columns(*columns)
        return await self.method.inject(query)


class ChunkMethodValidator(DisplayMethodValidator):
    """Validation for `ChunkMethod`."""

    method: ChunkMethod

    async def inject_raw(self, payload: Node) -> bytes:
        query = Query().columns(payload)
        payload = self.method.build_payload(query, position=0)
        return await self.method.inject(payload)


class TypedQueriesValidator(Validator):
    """Validates that queries of different types work fine."""

    name: str = "Typed queries"
    method: Method

    def __init__(self, method: Method, status: LiveStatus = None) -> None:
        super().__init__(status)
        self.method = method

    async def validate(self) -> None:
        """Verifies that the injection works fine by running test queries."""
        await super().validate()

        names_values_types = [
            ["text", "m", TextType()],
            ["int", 2108, IntType(min=2100, max=2110)],
            ["bool", True, BoolType()],
            ["null", None, TextType()],
        ]

        for name, value, tpe in names_values_types:
            out_prefix = f"**{name}**:"
            column = Value(value, type=tpe)
            query = Query().columns(column)

            try:
                results = await self.method.fetch(query)
            except BaseException as e:
                raise ValidationError(
                    f"{out_prefix} An error occured while fetching the results",
                    "Check the exception",
                )

            try:
                result = results.data[0][0]
            except IndexError:
                x = len(results.data)
                y = x and len(results.data[0])
                raise ValidationError(
                    f"{out_prefix} Result set has invalid size",
                    f"Size: {x}x{y}",
                )

            if result != value:
                raise ValidationError(
                    f"{out_prefix} Expected result differs from the actual result",
                    f"Expected result: `{value!r}`",
                    f"Obtained result: `{result!r}`",
                )

            self.status.success(f"{out_prefix} Successful query")


class DesignValidator(Validator, ABC):
    """Checks up part of a design."""

    design: Design

    def __init__(self, design: Design, status: LiveStatus = None) -> None:
        self.design = design
        super().__init__(status)


class DesignSetupValidator(DesignValidator):
    """Validates the setup phase of a design."""

    name: str = "Design: setup"

    async def validate(self) -> None:
        await super().validate()

        try:
            await self.design.setup()
        except BaseException as e:
            raise ValidationError(
                "Setup raises an exception",
                "Check the code of `Design.setup()` for errors",
            )
        else:
            self.status.success("Setup successful")


class DesignSetConfigurationValidator(DesignValidator):
    name: str = "Design: configuration"

    def _check_attribute_sane(self, name: str, cls: type) -> None:
        design = self.design
        cls_name = cls.__name__

        if not hasattr(design, name):
            raise ValidationError(
                f"**{name.title()}** is not defined",
                f"Verify that `Design.set_configuration()` sets `self.{name}`",
            )

        attribute = getattr(design, name)

        if attribute is None:
            raise ValidationError(
                f"**{name.title()}** is `None`",
                f"Verify that `Design.set_{name}()` returns a `{cls_name}`.",
            )

        if not isinstance(attribute, cls):
            raise ValidationError(
                f"`{attribute!r}` is not an instance of **{cls_name}**",
                f"Ensure that `Design.set_{name}()` returns a `{cls_name}`.",
            )

        self.status.success(f"**{name.title()}** is properly set")

    async def validate(self) -> None:
        await super().validate()
        try:
            await self.design.set_configuration()
        except BaseException as e:
            raise ValidationError(
                "Configuration raises an exception",
                "Check the code of `Design.setup()` for errors",
            )
        else:
            self.status.success("Configuration successful")

        self._check_attribute_sane("compiler", Compiler)
        self._check_attribute_sane("method", Method)
        self._check_attribute_sane("mapper", Mapper)


class DesignTeardownValidator(DesignValidator):
    """Validates the teardown phase of a design."""

    name: str = "Design: teardown"

    async def validate(self) -> None:
        await super().validate()

        try:
            await self.design.teardown()
        except BaseException as e:
            raise ValidationError(
                "Setup raises an exception",
                "Check the code of `Design.teardown()` for errors",
            )
        else:
            self.status.success("Teardown successful")


class QueryValidator(Validator):
    """Verifies that a query is correctly built."""

    name: str = "Query"
    method: Method
    query: Query

    def __init__(self, method: Method, query: Query, status: LiveStatus = None) -> None:
        super().__init__(status)
        self.method = method
        self.query = query

    async def _get_single_result(self, query: Query) -> Any:
        results = await self.method.fetch(query)
        return results.data[0][0]

    async def check_consistent(
        self, name: str, query: Query, solutions=[], exception=True
    ) -> bool:
        """Validates that a query is consistent by running two queries:
        one with COUNT() == 0 and one with COUNT() != 0.
        """
        query = query.distinct(False)
        q_count_is_zero = Query(Alias.randomized(query)).columns(Count() == 0)
        q_count_not_zero = Query(Alias.randomized(query)).columns(Count() != 0)

        self.method.compiler.wrap(q_count_is_zero)
        self.method.compiler.wrap(q_count_not_zero)

        original_exception = None
        problem: str = None

        try:
            r_count_is_zero = await self._get_single_result(q_count_is_zero)
            r_count_not_zero = await self._get_single_result(q_count_not_zero)
        except BaseException as e:
            original_exception = e if exception else None
            problem = f"An exception occurred"
        else:
            if r_count_is_zero != r_count_not_zero:
                return r_count_not_zero

            problem = (
                f"Inconsistency: `COUNT(*)=0` and `COUNT(*)!=0` are both"
                f" `{r_count_is_zero}`"
            )

        # If we reach this point, there was a problem: build the exception
        raise ValidationError(
            f"{name}: invalid",
            problem,
            *solutions,
        ) from original_exception

    async def validate(self) -> None:
        await super().validate()

        # Efficiency is important: we cannot run full-blown injections for each
        # part of the query. We cantonate to only 2 injections (and hopefully)
        # requests per part of the query (table, columns, where, etc.)
        self.method.compiler.wrap(self.query)
        query = self.query

        # Check the table

        if query.q.table:
            q_table = Query(query.q.table).columns(Star())
            has_rows = await self.check_consistent(
                f"Table **{query.q.table}**",
                q_table,
                [
                    "Check that the table exists and that you have permissions to read the table"
                ],
            )

            if has_rows:
                self.status.success(f"Table **{query.q.table}**: exists (non empty)")
            else:
                self.status.success(f"Table **{query.q.table}**: exists (empty)")
        else:
            q_table = Query()
            has_rows = True

        # Check columns

        for column in query.q.columns:
            c_has_rows = await self.check_consistent(
                f"Column **{column}**", q_table.columns(column), exception=False
            )

            # COUNT(*) is zero, but COUNT(column) != 0
            # Don't see how this could happen, but since we have data to figure
            # it out...
            if c_has_rows and not has_rows:
                raise ValidationError(
                    f"Column **{column}**: column is not empty, but the table is",
                    "Check that the column name is correct",
                )
            self.status.success(f"Column **{column}**: valid")

        if query.q.where:
            await self.check_consistent("WHERE clause", query.order(None))
            self.status.success(f"WHERE clause: valid")

        if query.q.order:
            await self.check_consistent("ORDER BY clause", query.where(None))
            self.status.success(f"ORDER BY clause: valid")


class TestMethodValidator(MethodValidator):
    name: str = "TestMethod: syntax"

    async def validate(self) -> None:
        await super().validate()
        await self.method.setup_semaphores()
        await self._validate_tautologies()
        await self._validate_syntaxes()

    async def _validate_tautologies(self) -> None:
        id1 = Value(random.randrange(1000, 9999))
        id2 = Value(id1.value + 1)

        always_true = id1 == id1
        always_false = id1 == id2

        result_true = await self.method.inject(always_true)
        result_false = await self.method.inject(always_false)

        if result_true and not result_false:
            self.status.success(
                "Injection behaves **correctly** with tautologies and contradictions"
            )
            return

        if result_true is result_false:
            returned = "true" if result_true else "false"
            raise ValidationError(
                f"`inject()` always returns **{returned}**",
                "Check that the method behaves correctly",
            )
        if not result_true and result_false:
            raise ValidationError(
                "`inject()` returns true when it should return false, and false when it should return true",
                "The test is probably inverted",
            )

    async def _try_yes_no(self, yes: Node, no: Node) -> None:
        yes = await self.method.inject(yes)
        no = await self.method.inject(no)

        return (yes and not no), yes, no

    async def _validate_syntaxes(self):
        """Runs a few tests to check if the syntax is supported."""
        value = randomized.alpha(1)

        # String value

        character = Value(value)
        await self._validate_syntax(
            "string value",
            (character == character),
            (character != character),
            "Check that the quoting function is correct",
        )

        # Test ord

        number = Ord(character)
        await self._validate_syntax(
            "ord value",
            (number == ord(value)),
            (number != ord(value)),
            "ORD() might be blacklisted",
        )

        # Test substring

        substring = Substring(character, 0, 1)
        await self._validate_syntax(
            "substring",
            (substring == substring),
            (substring != substring),
            "SUBSTRING() might be blacklisted",
        )

        # Test comparisons

        number = random.randrange(100, 999)
        vnumber = Value(number)
        await self._validate_syntax(
            "comparison (>=, >)",
            (vnumber >= vnumber),
            (vnumber > vnumber),
            "Operators >= or > might be blacklisted, or HTML-entities might be escaped",
        )
        number = random.randrange(100, 999)
        vnumber = Value(number)
        await self._validate_syntax(
            "comparison (<=, <)",
            (vnumber <= vnumber),
            (vnumber < vnumber),
            "Operators <= or < might be blacklisted, or HTML-entities might be escaped",
        )

    async def _validate_syntax(
        self, type: str, test_yes: Node, test_no: Node, *solutions: str
    ) -> None:
        """Verifies that the syntax is supported by making sure `test_yes` is true and
        `test_no` is false. If not, raises a `ValidationError`.
        """
        ok, yes, no = await self._try_yes_no(test_yes, test_no)

        if ok:
            self.status.success(f"Syntax tests passed for {type}")
        else:
            raise ValidationError(
                f"Syntax tests failed for {type} ({yes=}, {no=})", *solutions
            )
