import hashlib
import html
import importlib
import itertools
import pathlib
import random
import re
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from unittest import mock

from ending.cli.design import Design
from ending.configuration import (
    RNG,
    Chain,
    ConditionInjector,
    ConfigurationException,
    DesignConfigurator,
    ErrorOracle,
    FormatInjector,
    Injector,
    Matcher,
    MatcherMap,
    NegativeMatcher,
    NoChainException,
    PositiveMatcher,
    SelectMethodConfigurator,
    SingleTestErrorOracle,
    StabilizedMatcher,
    DoubleTestErrorOracle,
    Hole,
    Sampler,
)
from ending.db import generic, sqlite
from ending.util import misc, quoting

from .fixtures import design


class TestRNG(unittest.TestCase):
    def test_rng_yields_all(self):
        full = [RNG() for _ in range(9)]
        self.assertEqual(set(full), set([11111 * i for i in range(1, 10)]))

    def test_rng_yields_in_same_order(self):
        full = [RNG() for _ in range(18)]
        self.assertEqual(full[:9], full[9:])

    def test_contradiction(self):
        full = RNG.contradiction()
        match = re.match(r"^(\d+)=(\d+)$", full)
        self.assertNotEqual(match.group(1), match.group(2))

    def test_tautology(self):
        full = RNG.tautology()
        match = re.match(r"^(\d+)=(\d+)$", full)
        self.assertEqual(match.group(1), match.group(2))


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = str(pathlib.Path(__file__).parent / "fixtures" / "schema.sqlite3")

    def setUp(self) -> None:
        self.maxDiff = None
        self.cnx = sqlite3.connect(self.db)
        self.cursor = self.cnx.cursor()
        return super().setUp()

    def tearDown(self) -> None:
        self.cursor.close()
        self.cnx.close()
        return super().tearDown()

    async def _execute(self, query: bytes, max_rows: int = None):
        try:
            self.cursor.execute(query)
        except sqlite3.DatabaseError as e:
            return "error", misc.to_bytes(str(e))
        return "results", b"".join(
            misc.to_bytes(item, none=b"")
            for i, row in enumerate(self.cursor)
            for item in row
            if max_rows is None or i < max_rows
        )


class FakeMatcher(Matcher):
    def matches(self, sample: bytes) -> bool:
        return sample == b"success"

    def negate(self) -> Matcher:
        return FakeNegatedMatcher()

    def is_positive(self) -> bool:
        return True


class FakeNegatedMatcher(Matcher):
    def matches(self, sample: bytes) -> bool:
        return sample != b"success"

    def negate(self) -> Matcher:
        return FakeMatcher()

    def is_positive(self) -> bool:
        return False


class TestErrorOracles(DatabaseTestCase):
    async def test_error_oracle_build_returns_single_test_oracle_for_error_matcher(
        self,
    ):
        async def send(payload: str) -> bytes:
            query = f"SELECT 1 WHERE {payload}"
            type_, data = await self._execute(query)
            return b"success" if type_ == "results" else b"error"

        injector = ConditionInjector(
            send=send,
            value="1",
            nb_parentheses=0,
            quote="",
            risky=False,
        )
        matchers: MatcherMap = {("success", "error"): FakeMatcher()}

        oracle = ErrorOracle.build(injector, matchers)

        self.assertIsInstance(oracle, SingleTestErrorOracle)
        self.assertFalse(oracle.injector.force_failure)

    async def test_error_oracle_build_returns_double_test_oracle_when_no_error_matcher(
        self,
    ):
        async def send(payload: str) -> bytes:
            query = f"SELECT 1 WHERE {payload}"
            type_, data = await self._execute(query)
            return b"success" if type_ == "results" else b"error"

        injector = ConditionInjector(
            send=send,
            value="1",
            nb_parentheses=0,
            quote="",
            risky=False,
        )
        matchers: MatcherMap = {("failure", "success"): FakeMatcher()}

        oracle = ErrorOracle.build(injector, matchers)

        self.assertIsInstance(oracle, DoubleTestErrorOracle)

    async def test_single_test_error_oracle_test(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT 1 WHERE {payload}"
            type_, data = await self._execute(query)
            return b"success" if type_ == "results" else b"error"

        injector = ConditionInjector(
            send=send,
            value="1",
            nb_parentheses=0,
            quote="",
            risky=False,
        )
        matcher = FakeMatcher()
        oracle = SingleTestErrorOracle(injector, matcher)

        self.assertTrue(await oracle.test())

    async def test_double_test_error_oracle_test(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT 1 WHERE {payload}"
            type_, data = await self._execute(query)
            return b"success" if type_ == "results" else b"error"

        injector = ConditionInjector(
            send=send,
            value="1",
            nb_parentheses=0,
            quote="",
            risky=False,
        )
        matcher = FakeMatcher()
        oracle = DoubleTestErrorOracle(injector, matcher)

        self.assertTrue(await oracle.test())


class TestMatchers(DatabaseTestCase):
    async def _check_injector_and_matchers(
        self,
        send,
        value,
        *,
        fails=False,
        quote=None,
        nb_parentheses=None,
        matchers=None,
    ) -> tuple[Injector, MatcherMap] | None:
        async def send_with_value(value=value) -> bytes:
            return await send(value)

        instance = design.Design()
        instance.send = send_with_value
        configurator = DesignConfigurator(instance, risky=False)
        configurator.base_value = value
        try:
            injector, _matchers = await configurator.get_injector_and_matchers()
        except ConfigurationException:
            if fails:
                return
            raise
        if fails:
            self.fail("Configuration should have failed")
        if quote is not None:
            self.assertEqual(injector.quote, quote)
        if nb_parentheses is not None:
            self.assertEqual(injector.nb_parentheses, nb_parentheses)
        if matchers is not None:
            self.assertEqual(
                set(
                    "-".join((a, b)) for (a, b), matcher in _matchers.items() if matcher
                ),
                set(matchers),
            )
        return injector, _matchers

    async def test_standard_output_yields_everything_with_no_quote(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return type.encode() + data

        await self._check_injector_and_matchers(
            send,
            "1",
            quote="",
            nb_parentheses=0,
            matchers=["success-failure", "failure-error", "success-error"],
        )

    async def test_standard_output_yields_everything_with_singlequote(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            return type.encode() + data

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            quote="'",
            nb_parentheses=0,
            matchers=["success-failure", "failure-error", "success-error"],
        )

    async def test_standard_output_yields_everything_with_doublequote(self):
        async def send(payload: str) -> bytes:
            query = f'SELECT * FROM information_schema__columns WHERE table_name="{payload}"'
            type, data = await self._execute(query)
            return type.encode() + data

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            quote='"',
            nb_parentheses=0,
            matchers=["success-failure", "failure-error", "success-error"],
        )

    async def test_standard_output_yields_everything_with_parentheses(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name IN ('{payload}')"
            type, data = await self._execute(query)
            return type.encode() + data

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            quote="'",
            nb_parentheses=1,
            matchers=["success-failure", "failure-error", "success-error"],
        )

    async def test_only_error_displayed(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name IN ('{payload}')"
            type, data = await self._execute(query)
            if type == "error":
                return data
            return b""

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            quote="'",
            nb_parentheses=1,
            matchers=["success-error", "failure-error"],
        )

    async def test_only_results_displayed(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            quote="'",
            nb_parentheses=0,
            matchers=["success-failure", "success-error"],
        )

    async def test_only_failure_displays_something(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            if type == "results" and data == b"":
                return b"failure!"
            return b""

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            quote="'",
            nb_parentheses=0,
            matchers=["success-failure", "failure-error"],
        )

    async def test_invalid_base_value_makes_configuration_fail(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        await self._check_injector_and_matchers(
            send,
            "tbl01dqzdqz",
            fails=True,
        )

    async def test_waf_blocks_and_keyword_makes_configuration_fail(self):
        async def send(payload: str) -> bytes:
            if " AND " in payload.upper():
                return b"error"
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        await self._check_injector_and_matchers(
            send,
            "tbl01",
            fails=True,
        )

    async def test_random_text_for_success_message_produces_negative_matchers(self):
        async def send(payload: str) -> bytes:
            if not hasattr(send, "i"):
                send.i = 0
            else:
                send.i += 1
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            if type == "error":
                return [b"ABC1", b"ABC2"][send.i % 2]
            return [b"RAN", b"DOM"][send.i % 2]

        injector, matchers = await self._check_injector_and_matchers(
            send,
            "tbl01",
        )
        self.assertFalse(matchers["success", "error"].is_positive())
        self.assertFalse(matchers["failure", "error"].is_positive())
        self.assertIsNone(matchers["success", "failure"])


class TestMatchersBehaviour(unittest.TestCase):
    def setUp(self) -> None:
        self.chain = Chain((Hole(), b"a", Hole()))
        return super().setUp()

    def test_positive_looks_for_pattern(self):
        matcher = PositiveMatcher(self.chain)
        self.assertTrue(matcher.matches(b"abc"))
        self.assertFalse(matcher.matches(b"bc"))

    def test_negative_excludes_pattern(self):
        matcher = NegativeMatcher(self.chain)
        self.assertFalse(matcher.matches(b"abc"))
        self.assertTrue(matcher.matches(b"bc"))

    def test_negate_changes_type(self):
        self.assertIsInstance(
            PositiveMatcher(self.chain).negate(),
            NegativeMatcher,
        )
        self.assertIsInstance(
            NegativeMatcher(self.chain).negate(),
            PositiveMatcher,
        )

    def test_is_positive(self):
        self.assertTrue(PositiveMatcher(self.chain).is_positive())
        self.assertFalse(NegativeMatcher(self.chain).is_positive())

    def test_stabilized_matcher_forwards_is_positive(self):
        self.assertTrue(
            StabilizedMatcher(PositiveMatcher(self.chain), None, None).is_positive()
        )
        self.assertFalse(
            StabilizedMatcher(NegativeMatcher(self.chain), None, None).is_positive()
        )


class AsyncTestMatchersBehaviour(unittest.IsolatedAsyncioTestCase):
    async def test_to_pattern_simplifies(self):
        sfrom = Sampler(None)
        sfrom.samples = [b"ABX", b"ACX", b"ADX", b"AEX"]
        sto = Sampler(None)
        sto.samples = [b"BB", b"BC", b"BD", b"BE"]

        matcher = await StabilizedMatcher.build(sfrom, sto)

        self.assertEqual(matcher.matcher.chain, Chain((b"A", Hole(), b"X")))
        self.assertTrue(matcher.is_positive())
        self.assertTrue(matcher.matcher.chain.to_pattern(), re.compile(b"^A.*X"))
        self.assertTrue(matcher.to_pattern(), re.compile(b"^A"))


class TestSamplers(unittest.IsolatedAsyncioTestCase):
    async def test_sample_yields_all(self):
        async def generator(i: int) -> int:
            return i

        sampler = Sampler(generator)
        self.assertEqual(
            await sampler.get(10),
            list(range(10)),
        )


class TestChain(unittest.TestCase):
    def _test_chain_is(self, chain: Chain, pattern: bytes):
        self.assertEqual(chain.to_pattern().pattern, pattern)

    def test_no_hole(self):
        self._test_chain_is(Chain((b"a",)), b"^a$")

    def test_hole_prefix(self):
        self._test_chain_is(Chain((Hole(), b"a")), b"a$")

    def test_hole_suffix(self):
        self._test_chain_is(Chain((b"a", Hole())), b"^a")

    def test_hole_surroundings(self):
        self._test_chain_is(Chain((Hole(), b"a", Hole())), b"a")

    def test_merge_different_chains_yields_nochainexception(self):
        with self.assertRaises(NoChainException):
            Chain.merge(
                Chain((b"a",)),
                Chain((b"b",)),
            )

    def test_merge_identical_chains_yields_same_chain(self):
        self.assertEqual(
            Chain.merge(
                Chain((b"a",)),
                Chain((b"a",)),
            ),
            Chain((b"a",)),
        )

    def test_simplify_does_not_find_small_size_pattern(self):
        x = b"a" * 100 + b"b"
        self.assertEqual(Chain.by_bytes(x).simplify([b"b" + x]), Chain((b"a", Hole())))

    def test_merge_identical_prefix_yield_prefixed_chain(self):
        self.assertEqual(
            Chain.merge(
                Chain((b"a", b"b")),
                Chain((b"a", b"c")),
            ),
            Chain((b"a", Hole())),
        )

    def test_merge_identical_suffix_yield_suffixed_chain(self):
        self.assertEqual(
            Chain.merge(
                Chain((b"a", b"c")),
                Chain((b"b", b"c")),
            ),
            Chain((Hole(), b"c")),
        )

    def test_merge_twice(self):
        self.assertEqual(
            Chain.merge(
                Chain((b"a", b"d")),
                Chain.merge(
                    Chain((b"a", b"d")),
                    Chain((b"c", b"d")),
                ),
            ),
            Chain((Hole(), b"d")),
        )

    def test_split_adds_holes(self):
        self.assertEqual(
            Chain((b"a", b"b")).split(),
            (
                Chain((b"a", Hole())),
                Chain((Hole(), b"b")),
            ),
        )

    def test_split_duplicates_holes(self):
        self.assertEqual(
            Chain((b"a", Hole(), b"b")).split(),
            (
                Chain((b"a", Hole())),
                Chain((Hole(), b"b")),
            ),
        )

    def test_no_links_yields_match_empty_pattern(self):
        self._test_chain_is(Chain(()), b"^$")

    def test_only_hole_yields_regex_that_matches_anything(self):
        self._test_chain_is(Chain((Hole(),)), b"")

    def split_is_clean_for_two_elements(self):
        self.assertEqual(
            Chain((b"a", b"b")).split(),
            (
                Chain((b"a",)),
                Chain((b"b",)),
            ),
        )
        with self.assertRaises(NoChainException):
            Chain((b"a", Hole())).split()
        with self.assertRaises(NoChainException):
            Chain((Hole(), b"b"))

    def split_is_clean_for_three_elements(self):
        self.assertEqual(
            Chain((Hole(), b"a", b"b")).split(),
            (
                Chain((Hole(), b"a")),
                Chain((b"b",)),
            ),
        )
        self.assertEqual(
            Chain((b"a", b"b", Hole())).split(),
            (
                Chain((b"a",)),
                Chain((b"b", Hole())),
            ),
        )
        with self.assertRaises(NoChainException):
            Chain((b"a", Hole())).split()
        with self.assertRaises(NoChainException):
            Chain((Hole(), b"b"))

    def test_simplify_is_not_slow(self):
        other = b"A" * 1000000
        base = bytearray(other)
        base[1000000 // 2] = ord("B")
        base = bytes(base)
        self._test_chain_is(Chain.by_bytes(base).simplify([other]), b"B")

    def test_prefix_gets_handled_properly(self):
        self._test_chain_is(Chain.from_samples([b"AX", b"AY"]).simplify([b"B"]), b"^A")

    def test_suffix_gets_handled_properly(self):
        self._test_chain_is(Chain.from_samples([b"XA", b"YA"]).simplify([b"B"]), b"A$")

    def test_does_not_test_again_same_chain(self):
        self._test_chain_is(
            Chain.from_samples([b"XAAAXAXAAAX", b"YAAAYAYAAAY"]).simplify([b"AAA"]),
            b"A.*AAA",
        )

    def test_chain_built_from_empty_strings_works(self):
        self.assertEqual(Chain.from_samples([b"", b""]), Chain(()))

    def test_empty_chain_excludes_anything_which_is_not_empty(self):
        self.assertTrue(Chain(()).excludes(b"A"))
        self.assertTrue(Chain(()).excludes(b"B"))
        self.assertTrue(Chain(()).matches(b""))


class TestDifferentiator(unittest.TestCase):
    def _test_has_no_contiguous_holes(self, chain: Chain):
        was_hole = False
        for item in chain.links:
            if item == Hole():
                if was_hole:
                    self.fail(f"Chain has two holes in a row: {chain}")
                was_hole = True
            else:
                was_hole = False

    def _test_matches(self, builders: list[bytes], ok: list[bytes], ko: list[bytes]):
        for permutation in itertools.permutations(builders):
            chain = Chain.from_samples(permutation)
            self._test_has_no_contiguous_holes(chain)
            chain = chain.simplify(ko)
            self._test_has_no_contiguous_holes(chain)
            pattern = chain.to_pattern()
            for sample in builders + ok:
                if pattern.search(sample) is None:
                    self.fail(
                        f"Pattern {pattern!r} does not match {sample!r} for permutation {permutation!r}"
                    )
            for sample in ko:
                if pattern.search(sample) is not None:
                    self.fail(
                        f"Pattern {pattern!r} matches {sample!r} for permutation {permutation!r}"
                    )

    def test_raw_matches(self):
        self._test_matches(
            [b"ABC"],
            [],
            [b"DEF"],
        )

    def test_raw_does_not_match_extra(self):
        self._test_matches(
            [
                b"ABC",
            ],
            [],
            [
                b"PREFIXABC",
                b"ABCSUFFIX",
            ],
        )

    def test_raw_does_not_match_extra(self):
        self._test_matches(
            [
                b"ABC",
            ],
            [],
            [
                b"PREFIXABC",
                b"ABCSUFFIX",
            ],
        )

    def test_with_some_random_prefix(self):
        self._test_matches(
            [
                b"RANDOMPREFIX-ABC",
                b"randomprefix-ABC",
            ],
            [
                b"RANdompreFIX-ABC",
            ],
            [
                b"RANDOMPREFIX-DEF",
                b"randomprefix-DEF",
            ],
        )

    def test_with_random_contents_of_varying_length(self):
        self._test_matches(
            [
                b"prefix-blugblug-MATCH",
                b"prefix-BOUBOU-MATCH",
            ],
            [
                b"prefix-BOUBOU-MATCH",
            ],
            [
                b"PREFIXABC",
                b"ABCSUFFIX",
            ],
        )

    def test_order_does_not_matter(self):
        self._test_matches(
            [
                b"prefix-blugblug-MATCH",
                b"prefix-BOUBOU-MATCH",
                b"prefix--MATCH",
                b"prefix----MATCH",
                b"prefi----MATCH",
            ],
            [],
            [],
        )

    def test_matches_arbitrary_suffix(self):
        self._test_matches(
            [
                b"prefix-blugblug-MATCH-1",
                b"prefix-BOUBOU-MATCH-2",
            ],
            [
                b"prefix-BOUBOU-MATCH-3",
                b"prefix-BOUBOU-MATCH-ABCDEF",
            ],
            [
                b"prefix-BOUBOU-NOT-MATCH",
            ],
        )

    def test_repeated_pattern_matches(self):
        self._test_matches(
            [
                b"123-123-",
                b"123-123-123-",
            ],
            [
                b"123-123-123-123-",
            ],
            [
                b"123-",
            ],
        )

    def test_repeated_pattern_matches_with_random_in_middle(self):
        self._test_matches(
            [
                b"123-hello-123-",
                b"123-there-123-123-",
            ],
            [
                b"123-123-123-123-",
            ],
            [
                b"123-",
            ],
        )

    def test_with_repeated_characters(self):
        self._test_matches(
            [
                b"AAX",
                b"AAY",
            ],
            [
                b"AAZ",
            ],
            [
                b"ABX",
                b"ABY",
            ],
        )

    def test_cannot_find_pattern_raises_exception(self):
        with self.assertRaises(NoChainException):
            Chain.from_samples([b"ABC", b"DEF"])

    def test_hole_repr_is_fine(self):
        self.assertEqual(repr(Hole()), "<HOLE>")

    def test_simplify_removes_useless_prefix_suffix(self):
        chain = Chain.from_samples([b"a b c", b"a b d"])
        self.assertEqual(
            chain.simplify([b"a c d"]).to_pattern().pattern,
            b"b",
        )

    def test_simplify_removes_useless_prefix_suffix_with_html(self):
        chain = Chain.from_samples([b"<title>Hi</title>OK!<end/>"])
        self.assertEqual(
            chain.simplify([b"<title>Hi</title>NOPE<end/>"]).to_pattern().pattern,
            b"OK!",
        )

    def test_simplify_removes_useless_prefix_midfix_suffix_with_html(self):
        chain = Chain.from_samples(
            [
                b"<title>Hi</title>OK!<MIDFIX/>Hi!<end/>",
                b"<title>Hi</title>OK!<MIDFIX2/><end/>",
            ]
        )
        self.assertEqual(
            chain.simplify([b"<title>Hi</title>NOPE<end/>"]).to_pattern().pattern,
            b"OK!",
        )

    def test_simplify_fails_with_same_contents(self):
        chain = Chain.from_samples([b"HELLO"])
        with self.assertRaises(NoChainException):
            chain.simplify([b"HELLO"])

    def test_simplify_on_identical_raises_exception_when_trimmable(self):
        chain = Chain.from_samples([b"HELLO<tag/>"])
        with self.assertRaises(NoChainException):
            chain.simplify([b"HELLO<tag/>"])

    def test_can_be_simplified_in_one_way_but_not_the_other(self):
        # We expect pattern to be AB.*, which matches B samples as well.
        # However, the second pattern is AB.*M, which does NOT match A samples
        a_samples = [b"ABC", b"ABD"]
        b_samples = [b"ABXM", b"ABYM"]

        chain = Chain.from_samples(a_samples)

        with self.assertRaises(NoChainException):
            chain.simplify(b_samples)

        chain = Chain.from_samples(b_samples)

        try:
            chain.simplify(a_samples)
        except NoChainException:
            self.fail("Should have been able to simplify")

    def test_split_does_not_produce_empty_fragments(self):
        chain = Chain._by_split(b"(<)", b"<<<<")
        self.assertEqual(
            chain.links,
            (
                b"<",
                b"<",
                b"<",
                b"<",
            ),
        )


class TestConditionInjectors(unittest.IsolatedAsyncioTestCase):
    def test_specialize(self):
        injector = ConditionInjector(None, "VALUE", 1, "'", risky=False)
        self.assertTrue(injector.enforce_failure().force_failure)
        self.assertFalse(injector.allow_condition().force_failure)

    def test_with_no_arguments(self):
        injector = ConditionInjector(None, "VALUE", 1, "'", risky=False)
        self.assertRegex(
            injector.build_payload(),
            r"^VALUE'\) AND \('\d+'!='\d+$",
        )
        injector = ConditionInjector(
            None, "VALUE", 1, "'", risky=False, force_failure=True
        )
        self.assertRegex(
            injector.build_payload(),
            r"^VALUE'\) AND \('\d+'='\d+$",
        )

    def test_with_condition(self):
        injector = ConditionInjector(None, "VALUE", 1, "'", risky=False)
        self.assertRegex(
            injector.build_payload("test=test"),
            r"^VALUE'\) AND test=test AND \('\d+'!='\d+$",
        )
        injector = ConditionInjector(
            None, "VALUE", 1, "'", risky=False, force_failure=True
        )
        self.assertRegex(
            injector.build_payload("test=test"),
            r"^VALUE'\) AND test=test AND \('\d+'='\d+$",
        )

    def test_with_condition_and_suffix(self):
        injector = ConditionInjector(None, "VALUE", 1, "'", risky=False)
        self.assertRegex(
            injector.build_payload("test=test", " some suffix"),
            r"^VALUE'\) AND test=test some suffix$",
        )
        injector = ConditionInjector(
            None, "VALUE", 1, "'", risky=False, force_failure=True
        )
        self.assertRegex(
            injector.build_payload("test=test", " some suffix"),
            r"^VALUE'\) AND test=test AND \d+=\d+ some suffix$",
        )

    def test_empty_suffix_is_not_equivalent_to_none(self):
        injector = ConditionInjector(None, "VALUE", 1, "'", risky=False)
        self.assertRegex(
            injector.build_payload("test=test", ""),
            r"^VALUE'\) AND test=test$",
        )


class TestFormatInjectors(unittest.IsolatedAsyncioTestCase):
    def test_specialize(self):
        injector = FormatInjector(None, "VALUE", "<{value}>X<{condition}>")
        self.assertTrue(injector.enforce_failure().force_failure)
        self.assertFalse(injector.allow_condition().force_failure)

    def test_with_no_arguments(self):
        injector = FormatInjector(None, "VALUE", "<{value}>X<{condition}>")
        self.assertRegex(
            injector.build_payload(),
            r"^<VALUE>X<\d+=\d+>$",
        )
        injector = FormatInjector(
            None, "VALUE", "<{value}>X<{condition}>", force_failure=True
        )
        self.assertRegex(
            injector.build_payload(),
            r"^<VALUE>X<\d+=\d+>$",
        )

    def test_with_condition(self):
        injector = FormatInjector(None, "VALUE", "<{value}>X<{condition}>")
        self.assertRegex(
            injector.build_payload("test=test"),
            r"^<VALUE>X<test=test>$",
        )
        injector = FormatInjector(
            None, "VALUE", "<{value}>X<{condition}>", force_failure=True
        )
        self.assertRegex(
            injector.build_payload("test=test"),
            r"^<VALUE>X<test=test AND \d+=\d+>$",
        )

    def test_with_condition_and_suffix(self):
        injector = FormatInjector(None, "VALUE", "<{value}>X<{condition}>")
        self.assertRegex(
            injector.build_payload("test=test", " some suffix"),
            r"^<VALUE>X<test=test> some suffix$",
        )
        injector = FormatInjector(
            None, "VALUE", "<{value}>X<{condition}>", force_failure=True
        )
        self.assertRegex(
            injector.build_payload("test=test", " some suffix"),
            r"^<VALUE>X<test=test AND \d+=\d+> some suffix$",
        )


class TestFullConfiguration(DatabaseTestCase):
    async def configure_and_get_design(
        self, send, risky: bool = False
    ) -> design.Design:
        """Copies the template design to a temporary location, loads it, and deletes it."""

        design_path = f"design{random.randrange(1, 1000000)}"
        fixtures = pathlib.Path(__file__).parent / "fixtures"
        test_designs = fixtures / "test_designs"
        if not test_designs.exists():
            test_designs.mkdir()
        design_path = test_designs / f"{design_path}.py"
        design_path.write_bytes((fixtures / "design.py").read_bytes())

        importlib.invalidate_caches()

        try:
            design = importlib.import_module(
                f"tests.fixtures.test_designs.{design_path.stem}"
            )
        except ModuleNotFoundError:
            if design_path.exists():
                text = design_path.read_text()
                design_path.unlink()
                raise RuntimeError(
                    f"Could not import design module {design_path} for test but the file exists"
                )
            raise RuntimeError(f"Could not import design module {design_path} for test")

        try:
            instance = design.Design()
            instance.send = send
            configurator = DesignConfigurator(instance, risky=risky)
            await configurator.configure()

            importlib.reload(design)
            return design.Design()
        finally:
            design_path.unlink()

    async def test_classic_union_based_with_one_row_returned(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query, max_rows=1)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 1)

    # NOTE There is NO error-based SQLite method, sadly
    # async def test_union_blocked_error_displayed_yields_errormethod(self):
    #     async def send(payload: str="1") -> bytes:
    #         if "UNION" in payload:
    #             return b"WAF"
    #         query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
    #         type, data = await self._execute(query)
    #         return type.encode() + data

    #     design = await self.configure_and_get_design(send)

    #     await design.setup()

    #     try:
    #         await design.set_configuration()
    #     finally:
    #         await design.teardown()

    #     self.assertIsInstance(design.compiler, sqlite.Compiler)
    #     self.assertIs(design.compiler.quote, quoting.singlequote)
    #     self.assertIsInstance(design.method, generic.ErrorBasedMethod)

    async def test_classic_union_based_with_no_rows_returned(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query, max_rows=0)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, sqlite.PragmaErrorMethod)

    async def test_classic_union_based(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_classic_union_based_with_failure_to_error_matcher(self):
        async def send(payload: str = 1) -> bytes:
            query = (
                f"SELECT {send.i} FROM information_schema__columns WHERE 1={payload}"
            )
            type, data = await self._execute(query)
            if type == "error" or data == str(send.i).encode():
                return b""
            if type == "results" and data == b"":
                return b"fail"
            send.i += 1
            return data

        send.i = 0

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 1)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_classic_union_based_with_in(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE column_name IN ('a', '{payload}')"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()

        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_original_query_with_invalid_base_value_is_not_configurable(self):
        async def send(payload: str = "2") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        with self.assertRaises(ConfigurationException) as cm:
            await self.configure_and_get_design(send)
        self.assertEqual(
            str(cm.exception), "Unable to distinguish outcomes; not injectable ?"
        )

    async def test_original_query_with_invalid_base_value_is_configurable_as_risky(
        self,
    ):
        async def send(payload: str = "2") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        design = await self.configure_and_get_design(send, risky=True)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, sqlite.TestMethod)

    async def test_completely_negated_query_is_configurable_as_risky(self):
        async def send(payload: str = "2") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1=0 AND 'a'='{payload}'"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        design = await self.configure_and_get_design(send, risky=True)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, sqlite.TestMethod)

    async def test_completely_negated_query_with_in_is_configurable_as_risky(self):
        async def send(payload: str = "2") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1=0 AND 'a' IN ('b', '{payload}')"
            type, data = await self._execute(query)
            if type == "results":
                return data
            return b""

        design = await self.configure_and_get_design(send, risky=True)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, sqlite.TestMethod)

    async def test_with_huge_number_of_parentheses_gives_errorbased(self):
        async def send(payload: str = "test") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE ((('test' IN ('a', '{payload}'))))"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, sqlite.PragmaErrorMethod)

    async def test_with_singlequote_escaped_yields_different_quoting_method(self):
        async def send(payload: str = "1") -> bytes:
            payload = payload.replace("'", "''")
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.char)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_union_with_limit_limits_number_of_rows(self):
        async def send(payload: str) -> bytes:
            if "#" in payload or "--" in payload:
                return b"WAF"
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query, max_rows=35)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 35)

    async def test_with_singlequote_and_doublequote_escaped_yields_different_quoting_method(
        self,
    ):
        async def send(payload: str) -> bytes:
            payload = payload.replace("'", "''")
            payload = payload.replace('"', '""')
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.char)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_no_output_yields_error(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE column_name='{payload}'"
            # We run the query, but we don't return anything
            type, data = await self._execute(query)
            return b""

        try:
            await self.configure_and_get_design(send)
        except ConfigurationException:
            pass
        else:
            self.fail("Configuration should have failed")

    async def test_with_dbms_that_has_only_selectmethod(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE column_name='{payload}'"
            # We run the query, but we don't return anything
            type, data = await self._execute(query)
            return data

        with mock.patch.object(sqlite.api, "__all__", []):
            with self.assertRaises(ConfigurationException) as cm:
                await self.configure_and_get_design(send)
            self.assertEqual(
                str(cm.exception), "Unable to configure an injection method"
            )

    async def test_with_cycling_output(self):
        rotations = itertools.cycle([b"1", b"2", b"3"])

        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)

            return next(rotations)

        with self.assertRaises(ConfigurationException) as cm:
            await self.configure_and_get_design(send)

        self.assertEqual(
            str(cm.exception), "Unable to distinguish outcomes; not injectable ?"
        )

    async def test_testmethod_with_success_failure_matcher(self):
        async def send(payload: str) -> bytes:
            if "UNION" in payload:
                return b"WAF"
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return data if type != "error" else b"error"

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_selectmethod_with_upper_results(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return data.upper()

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertTrue(design.method.hex)

    async def test_selectmethod_with_one_row(self):
        async def send(payload: str) -> bytes:
            if payload.count("UNION") > 1:
                return b"WAF"
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 1)
        self.assertFalse(design.method.hex)

    async def test_union_with_more_than_10_fails(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT 1,2,3,4,5,6,7,8,9,10,11 FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            if data != b"abc":
                data = b"abc"
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_union_with_results_displayed_thrice_works(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT 1,2,3 FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)

            return type.encode() + data + data + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertFalse(design.method.hex)

    async def test_selectmethod_with_empty_results(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT 'abc' FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            if data != b"abc":
                data = b"abc"
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_inject_in_order_by_direction(self):
        async def send(payload: str = "ASC") -> bytes:
            query = f"SELECT * FROM information_schema__columns ORDER BY column_name {payload}"
            type, data = await self._execute(query)
            return type.encode() + data if type == "results" else b""

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_inject_in_order_by_column(self):
        async def send(payload: str = "column_name") -> bytes:
            query = f"SELECT * FROM information_schema__columns ORDER BY {payload}"
            type, data = await self._execute(query)
            return type.encode() + data if type == "results" else b""

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_in_place_error_based(self):
        async def send(payload: str) -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE (CASE WHEN {payload} THEN 1/0 ELSE 0 END)"
            type, data = await self._execute(query)
            return type.encode()

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_injectable_but_no_db_found(self):
        async def send(payload: str = "1") -> bytes:
            if "sqlite_version()" in payload:
                return b"error"
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            return type.encode()

        with self.assertRaises(ConfigurationException) as cm:
            await self.configure_and_get_design(send)

        self.assertEqual(str(cm.exception), "Unable to determine DBMS")

    async def test_deny_every_quoting_mechanism(self):
        async def send(payload: str) -> bytes:
            if any(x in payload.lower() for x in ["'", '"', "char"]):
                return b"WAF"
            query = f"SELECT * FROM information_schema__columns WHERE (CASE WHEN {payload} THEN 1/0 ELSE 0 END)"
            type, data = await self._execute(query)
            return type.encode()

        with self.assertRaises(ConfigurationException) as cm:
            await self.configure_and_get_design(send)

        self.assertEqual(str(cm.exception), "Unable to determine SQL quoting mechanism")

    async def test_no_selectmethod(self):
        async def send(payload: str) -> bytes:
            if any(x in payload.lower() for x in ["'", '"', "char"]):
                return b"WAF"
            query = f"SELECT * FROM information_schema__columns WHERE (CASE WHEN {payload} THEN 1/0 ELSE 0 END)"
            type, data = await self._execute(query)
            return type.encode()

        with self.assertRaises(ConfigurationException) as cm:
            await self.configure_and_get_design(send)

        self.assertEqual(str(cm.exception), "Unable to determine SQL quoting mechanism")

    async def test_not_injectable_but_echos_input(self):
        async def send(payload: str) -> bytes:
            return payload.encode()

        with self.assertRaises(ConfigurationException) as cm:
            design = await self.configure_and_get_design(send)

        self.assertEqual(
            str(cm.exception), "Unable to determine a way to inject; not injectable ?"
        )

    async def test_send_has_zero_parameters_raises_configurationexception(self):
        async def send() -> bytes:
            return ""

        with self.assertRaises(ConfigurationException) as cm:
            design = await self.configure_and_get_design(send)

        self.assertEqual(str(cm.exception), "`send()` must have at least one parameter")

    async def test_configure_unconfigurable_design(self):
        with self.assertRaises(ConfigurationException) as cm:
            with mock.patch.object(design.Design, "send", Design.send):
                instance = design.Design()
                configurator = DesignConfigurator(instance, risky=False)
                await configurator.configure()
        self.assertEqual(str(cm.exception), "Design is not configurable")

    async def test_union_with_upper_case_sets_hex(self):
        async def send(payload: str = "tbl03") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            return type.encode() + data.upper()

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertTrue(design.method.hex)

    async def test_union_with_undeterminist_hex_test_works(self):
        async def send(payload: str = "tbl03") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            return type.encode() + data

        with mock.patch.object(
            SelectMethodConfigurator, "_configure_hex", return_value=False
        ):
            design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertFalse(design.method.hex)

    async def test_union_with_undeterminist_hex_test_and_upper_case_works(self):
        async def send(payload: str = "tbl03") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            return type.encode() + data.upper()

        with mock.patch.object(
            SelectMethodConfigurator, "_configure_hex", return_value=False
        ):
            design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertFalse(design.method.hex)

    async def test_configuration_with_no_value_obtained_fails(self):
        async def send(payload: str = "tbl03") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE table_name='{payload}'"
            type, data = await self._execute(query)
            return type.encode() + data if type == "results" else b""

        with mock.patch.object(
            SelectMethodConfigurator, "_fetch_value", return_value=None
        ):
            design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(
            design.pattern.pattern,
            b"^resultsdb01tbl03col01INTEGERdb01tbl03col02TEXTdb01tbl03col03BOOLEANdb02tbl03col01INTEGERdb02tbl03col02TEXTdb02tbl03col03BOOLEAN$",
        )

    async def test_configuration_with_failure_to_error_matcher(self):
        # Ensure that we get no match for success->failure or success->error
        successes = itertools.cycle((b"XAX", b"XBX"))
        failures = itertools.cycle((b"XABX", b"XACX"))
        errors = itertools.cycle((b"XBCX", b"XBDX"))

        async def send(payload: str = "1") -> bytes:
            # ugly trick that makes use of pointers
            query = f"SELECT * FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            if type == "error":
                return next(errors)
            if not data:
                return next(failures)
            return next(successes)

        with mock.patch.object(
            SelectMethodConfigurator, "_fetch_value", return_value=None
        ):
            design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(design.pattern.pattern, b"A")

    async def test_union_with_iterative_displayed_column(self):
        async def send(payload: str) -> bytes:
            # ugly trick that makes use of pointers
            query = f"SELECT * FROM information_schema__columns WHERE column_name IN ('a', '{payload}')"
            type, data = await self._execute(query)
            return type.encode() + data

        async def raise_configurationerror(*args, **kwargs):
            raise ConfigurationException()

        with mock.patch.object(
            SelectMethodConfigurator,
            "_find_displayed_column_fast",
            raise_configurationerror,
        ):
            design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_union_with_skipped_results_gives_nb_rows_1(self):
        async def send(payload: str) -> bytes:
            # ugly trick that makes use of pointers
            query = f"SELECT * FROM information_schema__columns WHERE column_name IN ('a', '{payload}')"
            try:
                self.cursor.execute(query)
            except sqlite3.DatabaseError as e:
                return b"error"
            return b"".join(
                misc.to_bytes(item, none=b"")
                for i, row in enumerate(self.cursor)
                for item in row
                if i % 2 == 0
            )

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 4)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 1)

    async def test_union_with_randomized_error_message(self):
        async def send(payload: str = "1") -> bytes:
            query = f"SELECT column_name, table_name FROM information_schema__columns WHERE 1={payload}"
            type, data = await self._execute(query)
            if type == "error":
                return str(random.randint(0, 9)).encode()
            return data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 2)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertFalse(design.method.hex)

    async def test_union_blocks_comments(self):
        async def send(payload: str = "1") -> bytes:
            if "--" in payload or "#" in payload:
                return b"WAF"
            query = (
                f"SELECT column_name FROM information_schema__columns WHERE 1={payload}"
            )
            type, data = await self._execute(query)
            return data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 1)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertFalse(design.method.hex)

    async def test_union_on_html_encode_works_with_alternative_quoting(self):
        async def send(payload: str = "1") -> bytes:
            payload = payload.replace("<", "&lt;")
            query = (
                f"SELECT column_name FROM information_schema__columns WHERE 1={payload}"
            )
            type, data = await self._execute(query)
            return data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.char)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 1)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertFalse(design.method.hex)

    async def test_union_on_html_encode_works_with_hex(self):
        async def send(payload: str = "1") -> bytes:
            query = (
                f"SELECT column_name FROM information_schema__columns WHERE 1={payload}"
            )
            type, data = await self._execute(query)
            return html.escape(data.decode()).encode()

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 1)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)
        self.assertTrue(design.method.hex)

    async def test_has_huge_number_of_columns(self):
        nb_columns = 100
        columns = ",".join(map(str, range(nb_columns)))

        async def send(payload: str) -> bytes:
            query = f"SELECT {columns} WHERE 1={payload}"
            type, data = await self._execute(query)
            return data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 100)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_classic_test_based_with_failure_to_error_matcher(self):
        async def send(payload: str = 1) -> bytes:
            if "UNION" in payload:
                return b""
            query = (
                f"SELECT {send.i} FROM information_schema__columns WHERE 1={payload}"
            )
            type, data = await self._execute(query)
            if type == "error":
                return b""
            if data == str(send.i).encode():
                return b"fail"
            if type == "results" and data == b"":
                return b"fail"
            send.i += 1
            return data

        send.i = 0

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)

    async def test_in_update_where(self):
        async def send(payload: str = 1) -> bytes:
            query = f"UPDATE information_schema__columns SET table_name=table_name WHERE 1={payload}"
            type, data = await self._execute(query)
            return type.encode() + data if type == "results" else b""

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(design.pattern.pattern, b"^results$")

    async def test_in_update_set(self):
        async def send(payload: str = 1) -> bytes:
            query = f"UPDATE information_schema__columns SET table_name='{payload}' WHERE 1=1"
            type, data = await self._execute(query)
            return type.encode() + data if type == "results" else b""

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(design.pattern.pattern, b"^results$")

    async def test_in_delete_where(self):
        async def send(payload: str = "some-column") -> bytes:
            query = f"INSERT INTO information_schema__columns VALUES ('some-db', 'some-table', 'some-column', 'some-type')"
            type, data = await self._execute(query)
            query = (
                f"DELETE FROM information_schema__columns WHERE column_name='{payload}'"
            )
            type, data = await self._execute(query)
            return type.encode() + data if type != "error" else b"error"

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(design.pattern.pattern, b"^results$")

    async def test_inplace_injection_insert(self):
        async def send(payload: str) -> bytes:
            query = f"INSERT INTO information_schema__columns VALUES ('some-db', 'some-table', 'some-column'-{payload}, 'some-type')"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, sqlite.PragmaErrorMethod)

    async def test_inplace_injection_select_field_as_first_field(self):
        # This would not work if a field was before, because without the FROM, we'd have
        # an unknown column error
        async def send(payload: str) -> bytes:
            query = f"SELECT {payload}, table_name FROM information_schema__tables"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        # Yes, one: we're getting rid of the end of the query
        self.assertEqual(len(design.method.columns), 1)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_inplace_injection_select_where_field(self):
        async def send(payload: str = "column_name") -> bytes:
            query = f"SELECT table_schema, table_name FROM information_schema__columns WHERE {payload}='tbl01'"
            type, data = await self._execute(query)
            return type.encode() + data

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.SelectMethod)
        self.assertEqual(len(design.method.columns), 2)
        self.assertEqual(design.method.columns[0].value, None)
        self.assertEqual(design.method.column, 0)
        self.assertEqual(design.method.nb_rows, 100)

    async def test_in_insert_values_as_string(self):
        async def send(payload: str = "some-column") -> bytes:
            query = f"INSERT INTO information_schema__columns VALUES ('some-db', 'some-table', '{payload}', 'some-type')"
            type, data = await self._execute(query)
            return type.encode() + data if type != "error" else b"error"

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(design.pattern.pattern, b"^results$")

    async def test_in_insert_values_as_int(self):
        async def send(payload: str = "1") -> bytes:
            query = f"INSERT INTO information_schema__columns VALUES ('some-db', 'some-table', {payload}, 'some-type')"
            type, data = await self._execute(query)
            return type.encode() + data if type != "error" else b"error"

        design = await self.configure_and_get_design(send)

        await design.setup()

        try:
            await design.set_configuration()
        finally:
            await design.teardown()

        self.assertIsInstance(design.compiler, sqlite.Compiler)
        self.assertIs(design.compiler.quote, quoting.singlequote)
        self.assertIsInstance(design.method, generic.TestMethod)
        self.assertEqual(design.pattern.pattern, b"^results$")

    async def test_with_additional_random_output_works_anyways(self):
        async def send(payload: str = "some-column") -> bytes:
            query = f"SELECT * FROM information_schema__columns WHERE column_name='{payload}'"
            type, data = await self._execute(query)
            return random.randbytes(100) + type.encode() + random.randbytes(100) + data

        # Try several times to ensure that it works eventually
        for _ in range(10):
            try:
                design = await self.configure_and_get_design(send)
            except ConfigurationException:
                continue

            await design.setup()

            try:
                await design.set_configuration()
            finally:
                await design.teardown()

            try:
                self.assertIsInstance(design.compiler, sqlite.Compiler)
                self.assertIs(design.compiler.quote, quoting.singlequote)
                self.assertIsInstance(design.method, generic.SelectMethod)
            except AssertionError:
                continue
            else:
                break
        else:
            self.fail("Could not configure design")
