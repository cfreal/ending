import re
from abc import ABC, abstractmethod
from typing import Callable
from unittest import IsolatedAsyncioTestCase, TestCase

from ending.ast import Comparison, Identifier, Node, Ord, Query, Substring, Value
from ending.cli.design import Design
from ending.db import mysql
from ending.db.generic.method import *
from ending.struct.livestatus import LiveStatus
from ending.struct.metadata import Context
from ending.util import quoting
from ending.validation import *


class TestLiveStatus(LiveStatus):
    def __init__(self):
        self.messages = []

    def start(self) -> None:
        pass

    def done(self) -> None:
        pass

    def status(self, message: str) -> None:
        pass

    def section(self, name: str, message: str = None) -> None:
        pass

    def info(self, message: str) -> None:
        self.messages.append(message)

    def success(self, message: str) -> None:
        self.messages.append(message)

    def failure(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        return self.messages.append(message)


class ValidationErrorTest(TestCase):
    def test_add_problem(self):
        a = ValidationError("err_1", "sol_1_1", "sol_1_2")
        a.add_problem_solutions("err_2", "sol_2_1", "sol_2_2")
        self.assertEqual(
            a.problems,
            {"err_1": ["sol_1_1", "sol_1_2"], "err_2": ["sol_2_1", "sol_2_2"]},
        )

    def test_merge(self):
        a = ValidationError("err_1", "sol_1_1", "sol_1_2")
        b = ValidationError("err_2", "sol_2_1", "sol_2_2")
        c = a + b
        self.assertEqual(
            c.problems,
            {"err_1": ["sol_1_1", "sol_1_2"], "err_2": ["sol_2_1", "sol_2_2"]},
        )


class ValidationTest(ABC):
    async def asyncSetUp(self) -> None:
        # Hexadecimal is needed: otherwise, the `_has_value()` method will fail
        self.status = TestLiveStatus()

    # We're making a leap here: we expect the method to produce proper messages (using
    # Status), and use exceptions as sole demonstration of failure.
    async def _expect_error(self, validator: Validator, message: str):
        with self.assertRaises(ValidationError) as cm:
            await validator.validate()
        self.assertEqual(str(cm.exception), message)
        return cm

    async def _expect_success(self, validator: Validator, messages: list[str]) -> None:
        try:
            await validator.validate()
        except ValidationError as e:
            self.fail("The validator should not have failed")

        self.assertEqual(messages, self.status.messages)

    @abstractmethod
    def get_validator(self, **kwargs): ...

    async def expect_success(self, messages: list[str], *args, **kwargs) -> None:
        validator = self.get_validator(*args, **kwargs)
        return await self._expect_success(validator, messages)

    async def expect_error(self, message: str, *args, **kwargs) -> None:
        validator = self.get_validator(*args, **kwargs)
        return await self._expect_error(validator, message)


class InjectValidationTest(ValidationTest, ABC):
    async def asyncSetUp(self) -> None:
        # Hexadecimal is needed: otherwise, the `_has_value()` method will fail
        self.compiler = mysql.Compiler(quote=quoting.hexadecimal)
        await super().asyncSetUp()

    @abstractmethod
    def get_validator(self, inject: Callable) -> Validator:
        """Returns an instance of the validator."""


class MethodValidatorTest(InjectValidationTest):
    async def test_solution_params(self):
        async def inject(payload: Node) -> bytes:
            return b""

        validator = self.get_validator(inject)
        self.assertEqual(
            validator._solution_params("param1", "param2"),
            f"Check the `param1`, `param2` parameter(s) of the method",
        )


class DisplayMethodValidatorTest(MethodValidatorTest):
    def between_markers(self, *items: list[str]) -> bytes:
        return (
            DisplayMethodValidator.MARKER_START
            + DisplayMethodValidator.MARKER_SEP.join(items)
            + DisplayMethodValidator.MARKER_END
        ).encode()

    # Result count

    async def test_response_does_not_contain_payload(self):
        async def inject(payload: Node) -> bytes:
            return b"nothing of interest"

        await self.expect_error("The results are **not reflected** in the page", inject)

    async def test_errors_are_merged(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("&lt;", "\\'", "\n")

        cm = await self.expect_error(
            "The results are **HTML-escaped**\nSingle quotes are **escaped** using a **backslash** (`\\'`)",
            inject,
        )
        self.assertEqual(
            cm.exception.problems,
            {
                "The results are **HTML-escaped**": [
                    "Convert columns to hexadecimal (`hex=True`)",
                    "Use `html.unescape()` on the response",
                ],
                "Single quotes are **escaped** using a **backslash** (`\\'`)": [
                    "Remove quote escaping from the response"
                ],
            },
        )

    async def test_response_contains_payload_several_times(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "\n") * 4

        await self.expect_success(
            [
                "The results are reflected **4 times** in the page",
                "The results' case is **not** modified",
                "HTML characters are **not** escaped or removed",
                "Single quotes are **not** escaped or removed",
                "Newlines are **not** escaped or removed",
            ],
            inject,
        )

    # Markers

    async def test_marker_start_is_lowercased(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "\n").lower()

        await self.expect_error("The results are displayed as **lowercase**", inject)

    async def test_marker_start_is_uppercased(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "\n").upper()

        await self.expect_error("The results are displayed as **uppercase**", inject)

    # Modification

    async def test_open_tag_is_html_encoded(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("&lt;", "'", "\n")

        await self.expect_error("The results are **HTML-escaped**", inject)

    async def test_open_tag_is_removed(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("", "'", "\n")

        await self.expect_error("`<` are **removed** from the response", inject)

    async def test_singlequote_is_escaped_with_backslash(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "\\'", "\n")

        await self.expect_error(
            "Single quotes are **escaped** using a **backslash** (`\\'`)", inject
        )

    async def test_singlequote_is_escaped_with_other_singlequote(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "''", "\n")

        await self.expect_error(
            "Single quotes are **escaped** using another **single quote** (`''`)",
            inject,
        )

    async def test_singlequote_is_removed(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "", "\n")

        await self.expect_error("`'` are **removed** from the response", inject)

    async def test_newline_is_escaped_as_space(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", " ")

        await self.expect_error(
            "Newlines (`'\\n'`) are **converted** to spaces (`' '`)", inject
        )

    async def test_less_than_is_reflected_as_something_random(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("HELLO", "'", "\n")

        await self.expect_error(
            "The test character `<` is reflected as `HELLO`", inject
        )

    async def test_quote_is_reflected_as_something_random(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "HELLO", "\n")

        await self.expect_error(
            "The test character `'` is reflected as `HELLO`", inject
        )

    async def test_less_than_is_reflected_as_something_random(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "HELLO")

        await self.expect_error(
            "The test character `\\n` is reflected as `HELLO`", inject
        )

    def _has_value(self, payload: Node, value: str) -> bool:
        """Returns whether the payload contains the given value."""
        return (
            re.search(f"0x([a-f0-9]{{2}})*{ord(value):02x}", str(payload)) is not None
        )

    async def test_injection_blocks_quotes(self):
        async def inject(payload: Node) -> bytes:
            if self._has_value(payload, "'"):
                return b"nothing"
            return self.between_markers("<", "\n")

        await self.expect_error(
            r"""Unable to get output when injecting these characters: `'`""", inject
        )

    async def test_injection_blocks_html(self):
        async def inject(payload: Node) -> bytes:
            if self._has_value(payload, "<"):
                return b"nothing"
            return self.between_markers("<", "'", "\n")

        await self.expect_error(
            r"""Unable to get output when injecting these characters: `<`""", inject
        )

    async def test_injection_blocks_html_and_quotes(self):
        async def inject(payload: Node) -> bytes:
            if self._has_value(payload, "<") or self._has_value(payload, "'"):
                return b"nothing"
            return self.between_markers("<", "'", "\n")

        await self.expect_error(
            r"""Unable to get output when injecting these characters: `<`, `'`""",
            inject,
        )

    async def test_injection_blocks_html_and_quotes_with_singlequote_encoding(self):
        self.compiler = mysql.Compiler(quote=quoting.singlequote_backslash)

        async def inject(payload: Node) -> bytes:
            if "\\'" in str(payload):
                return b"nothing"
            return self.between_markers("<", "'", "\n")

        await self.expect_error(
            r"""Unable to get output when injecting these characters: `'`""", inject
        )

    async def test_injection_works_one_by_one_but_not_with_all_at_once(self):
        async def inject(payload: Node) -> bytes:
            if all(self._has_value(payload, c) for c in "<'\n"):
                return b"nothing"
            return self.between_markers("<", "'", "\n")

        await self.expect_error(
            r"""Although injecting `\n` one-by-one works, injecting them all at once fails""",
            inject,
        )

    async def test_injection_somehow_returns_several_results_if_there_is_a_quote(self):
        # Super, super unlikely, but handled by the validator code
        async def inject(payload: Node) -> bytes:
            if self._has_value(payload, "'"):
                if not self._has_value(payload, "<"):
                    # Only a single quote is sent: send results twice
                    return self.between_markers("'") * 2
                # Single quote and others sent: send back nothing
                return b"nothing"
            # Otherwise, send empty results
            return self.between_markers()

        await self.expect_error(
            r"""Injecting `'` yields **2** results instead of **1**""", inject
        )

    async def test_returns_strange_data(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "\n", "added")

        await self.expect_error(
            r"""The data is **not** reflected as expected: `[b'<', b"'", b'\n', b'added']` != `['<', "'", '\n']`""",
            inject,
        )

    async def test_valid_method_yields_correct_messages(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "\n")

        await self.expect_success(
            [
                "The results are reflected **once** in the page",
                "The results' case is **not** modified",
                "HTML characters are **not** escaped or removed",
                "Single quotes are **not** escaped or removed",
                "Newlines are **not** escaped or removed",
            ],
            inject,
        )

    async def test_md_repr_raises_exception_when_type_is_invalid(self):
        async def inject(payload: Node) -> bytes:
            return self.between_markers("<", "'", "\n")

        validator = self.get_validator(inject)

        with self.assertRaises(TypeError):
            validator._md_repr(3)


class SelectMethodValidatorTest(DisplayMethodValidatorTest, IsolatedAsyncioTestCase):
    def get_validator(self, inject: Callable) -> MethodValidator:
        m = SelectMethod(
            self.compiler,
            inject,
            columns=5,
            column=1,
            nb_rows=1,
        )
        return m.get_validator()(m, status=self.status)


class ChunkMethodValidatorTest(DisplayMethodValidatorTest, IsolatedAsyncioTestCase):
    def get_validator(self, inject: Callable) -> MethodValidator:
        m = ChunkMethod(self.compiler, inject)
        return m.get_validator()(m, status=self.status)


class TestMethodValidatorTest(MethodValidatorTest, IsolatedAsyncioTestCase):
    def get_validator(self, inject: Callable) -> MethodValidator:
        m = TestMethod(self.compiler, inject)
        return m.get_validator()(m, status=self.status)

    async def test_always_true(self):
        async def inject(payload: Node) -> bytes:
            return True

        await self.expect_error("`inject()` always returns **true**", inject)

    async def test_always_false(self):
        async def inject(payload: Node) -> bytes:
            return False

        await self.expect_error("`inject()` always returns **false**", inject)

    async def test_inverted_results(self):
        async def inject(payload: Node) -> bytes:
            match payload:
                case Comparison(left=Value(value=x), operator="=", right=Value(y)):
                    return x != y
                case Comparison(left=Value(value=x), operator="!=", right=Value(y)):
                    return x == y
                # This is not required as the configuration does not go this far
                # case Comparison(
                #     left=Ord(Value(value=x)), operator="=", right=Value(y)
                # ):
                #     return ord(x) != y
                # case Comparison(
                #     left=Ord(Value(value=x)), operator="!=", right=Value(y)
                # ):
                #     return ord(x) == y
                # case Comparison(
                #     left=Substring(Value(value=x), 0, 1),
                #     operator="=",
                #     right=Substring(Value(y), 0, 1),
                # ):
                #     return x != y
                # case Comparison(
                #     left=Substring(Value(value=x), 0, 1),
                #     operator="!=",
                #     right=Substring(Value(y), 0, 1),
                # ):
                #     return x == y
                # case Comparison(left=Value(value=x), operator=">=", right=Value(y)):
                #     return x < y
                # case Comparison(left=Value(value=x), operator=">", right=Value(y)):
                #     return x <= y
                case _:
                    raise ValueError(f"unexpected payload: {payload!r}")

        await self.expect_error(
            "`inject()` returns true when it should return false, and false when it should return true",
            inject,
        )

    async def test_invalid_ord_results(self):
        async def inject(payload: Node) -> bytes:
            match payload:
                case Comparison(left=Value(value=x), operator="=", right=Value(y)):
                    return x == y
                case Comparison(left=Value(value=x), operator="!=", right=Value(y)):
                    return x != y
                case Comparison(left=Ord(Value(value=x)), operator="=", right=Value(y)):
                    return ord(x) != y
                case Comparison(
                    left=Ord(Value(value=x)), operator="!=", right=Value(y)
                ):
                    return ord(x) == y
                case _:
                    raise ValueError(f"unexpected payload: {payload!r}")

        await self.expect_error(
            "Syntax tests failed for ord value (yes=False, no=True)",
            inject,
        )

    async def test_valid_method_yields_correct_messages(self):
        async def inject(payload: Node) -> bytes:
            match payload:
                case Comparison(left=Value(value=x), operator="=", right=Value(y)):
                    return x == y
                case Comparison(left=Value(value=x), operator="!=", right=Value(y)):
                    return x != y
                case Comparison(left=Ord(Value(value=x)), operator="=", right=Value(y)):
                    return ord(x) == y
                case Comparison(
                    left=Ord(Value(value=x)), operator="!=", right=Value(y)
                ):
                    return ord(x) != y
                case Comparison(
                    left=Substring(Value(value=x), 0, 1),
                    operator="=",
                    right=Substring(Value(y), 0, 1),
                ):
                    return x == y
                case Comparison(
                    left=Substring(Value(value=x), 0, 1),
                    operator="!=",
                    right=Substring(Value(y), 0, 1),
                ):
                    return x != y
                case Comparison(left=Value(value=x), operator=">=", right=Value(y)):
                    return x >= y
                case Comparison(left=Value(value=x), operator=">", right=Value(y)):
                    return x > y
                case Comparison(left=Value(value=x), operator="<=", right=Value(y)):
                    return x <= y
                case Comparison(left=Value(value=x), operator="<", right=Value(y)):
                    return x < y
                case _:
                    raise ValueError(f"unexpected payload: {payload!r}")

        await self.expect_success(
            [
                "Injection behaves **correctly** with tautologies and contradictions",
                "Syntax tests passed for string value",
                "Syntax tests passed for ord value",
                "Syntax tests passed for substring",
                "Syntax tests passed for comparison (>=, >)",
                "Syntax tests passed for comparison (<=, <)",
            ],
            inject,
        )


class SimpleMethod(Method):
    """A fake method that returns one result."""

    async def fetch_results(self, query: Query, ctx: Context):
        return await self.inject(query)


class TypedQueriesValidationTest(InjectValidationTest, IsolatedAsyncioTestCase):
    def get_validator(self, inject: Callable) -> Method:
        m = SimpleMethod(self.compiler, inject)
        return TypedQueriesValidator(m, status=self.status)

    async def test_valid_method_yields_correct_messages(self):
        async def inject(query: Query):
            return [[query.q.columns[0].value]]

        await self.expect_success(
            [
                "**text**: Successful query",
                "**int**: Successful query",
                "**bool**: Successful query",
                "**null**: Successful query",
            ],
            inject,
        )

    async def test_error_on_text_raises_exception(self):
        async def inject(query: Query):
            raise ValueError("Error")

        await self.expect_error(
            "**text**: An error occured while fetching the results", inject
        )

    async def test_invalid_size_on_text_raises_exception(self):
        async def inject(query: Query):
            return []

        message = "**text**: Result set has invalid size"
        cm = await self.expect_error(message, inject)
        self.assertEqual(cm.exception.problems[message], ["Size: 0x0"])

    async def test_invalid_size_on_text_raises_exception(self):
        async def inject(query: Query):
            return []

        message = "**text**: Result set has invalid size"
        cm = await self.expect_error(message, inject)
        self.assertEqual(cm.exception.problems[message], ["Size: 0x0"])

    async def test_invalid_result_on_text_raises_exception(self):
        async def inject(query: Query):
            return [["toto"]]

        message = "**text**: Expected result differs from the actual result"
        cm = await self.expect_error(
            message,
            inject,
        )
        self.assertEqual(
            cm.exception.problems[message],
            [
                "Expected result: `'m'`",
                "Obtained result: `'toto'`",
            ],
        )


class QueryValidationTest(InjectValidationTest, IsolatedAsyncioTestCase):
    # Rewrite the two methods for them to receive a Query

    def get_validator(self, inject: Callable, query: Query) -> Method:
        m = SimpleMethod(self.compiler, inject)
        return QueryValidator(m, query, status=self.status)

    async def test_valid_query_with_table_yields_correct_messages(self):
        async def inject(query: Query):
            return [["!=" in str(query)]]

        await self.expect_success(
            [
                "Table **users**: exists (non empty)",
                "Column **name**: valid",
                "Column **age**: valid",
                "Column **is_admin**: valid",
                "Column **address**: valid",
            ],
            inject,
            Query("users").columns("name", "age", "is_admin", "address"),
        )

    async def test_valid_query_with_empty_table_yields_correct_messages(self):
        async def inject(query: Query):
            return [["!=" not in str(query)]]

        await self.expect_success(
            [
                "Table **users**: exists (empty)",
                "Column **name**: valid",
                "Column **age**: valid",
                "Column **is_admin**: valid",
                "Column **address**: valid",
            ],
            inject,
            Query("users").columns("name", "age", "is_admin", "address"),
        )

    async def test_valid_query_without_table_yields_correct_messages(self):
        async def inject(query: Query):
            return [["!=" in str(query)]]

        await self.expect_success(
            [
                "Column **name**: valid",
                "Column **age**: valid",
                "Column **is_admin**: valid",
                "Column **address**: valid",
            ],
            inject,
            Query().columns("name", "age", "is_admin", "address"),
        )

    async def test_valid_query_with_table_and_order_and_where_yields_correct_messages(
        self,
    ):
        async def inject(query: Query):
            return [["!=" in str(query)]]

        await self.expect_success(
            [
                "Column **name**: valid",
                "Column **age**: valid",
                "Column **is_admin**: valid",
                "Column **address**: valid",
                "WHERE clause: valid",
                "ORDER BY clause: valid",
            ],
            inject,
            Query()
            .columns("name", "age", "is_admin", "address")
            .where(Identifier("id") == 1)
            .order("name"),
        )

    async def test_check_consistent_with_inconsistent_results(self):
        async def inject(query: Query):
            return [[True]]

        query = Query().columns("name")
        validator = self.get_validator(inject, query)

        with self.assertRaises(ValidationError) as cm:
            await validator.check_consistent("test", query)
        message = "test: invalid"
        self.assertEqual(message, str(cm.exception))
        self.assertEqual(
            cm.exception.problems[message],
            ["Inconsistency: `COUNT(*)=0` and `COUNT(*)!=0` are both `True`"],
        )

    async def test_check_consistent_with_consistent_results(self):
        async def inject(query: Query):
            return [["!=" in str(query)]]

        query = Query().columns("name")
        validator = self.get_validator(inject, query)
        await validator.check_consistent("test", query)

    async def test_check_consistent_with_exception(self):
        async def inject(query: Query):
            raise ValueError("whatever")

        query = Query().columns("name")
        validator = self.get_validator(inject, query)
        with self.assertRaises(ValidationError) as cm:
            await validator.check_consistent("test", query)
        self.assertEqual("test: invalid", str(cm.exception))
        self.assertEqual(
            "An exception occurred", cm.exception.problems["test: invalid"][0]
        )

    async def test_query_with_one_inconsistent_column(self):
        async def inject(query: Query):
            if "is_admin" in str(query):
                return [[True]]
            return [["!=" in str(query)]]

        await self.expect_error(
            "Column **is_admin**: invalid",
            inject,
            Query("users").columns("name", "age", "is_admin", "address"),
        )

    async def test_query_table_is_empty_but_column_is_not(self):
        async def inject(query: Query):
            if "is_admin" in str(query):
                return [["!=" in str(query)]]
            return [["!=" not in str(query)]]

        await self.expect_error(
            "Column **is_admin**: column is not empty, but the table is",
            inject,
            Query("users").columns("name", "age", "is_admin", "address"),
        )


class DesignTeardownValidationTest(ValidationTest, IsolatedAsyncioTestCase):
    def get_validator(self, design: Design):
        return DesignTeardownValidator(design, status=self.status)

    async def test_teardown_is_fine(self):
        class TestDesign(Design):
            async def setup(self):
                pass

            async def teardown(self):
                pass

        await self.expect_success(["Teardown successful"], TestDesign())

    async def test_teardown_with_exception_produces_problem(self):
        class TestDesign(Design):
            async def setup(self):
                pass

            async def teardown(self):
                raise ValueError("whatever")

        await self.expect_error("Setup raises an exception", TestDesign())


class DesignSetupValidationTest(ValidationTest, IsolatedAsyncioTestCase):
    def get_validator(self, design: Design):
        return DesignSetupValidator(design, status=self.status)

    async def test_setup_raises_exception(self):
        class TestDesign(Design):
            async def setup(self):
                raise ValueError("woops")

        await self.expect_error("Setup raises an exception", TestDesign())

    async def test_setup_works_fine(self):
        class TestDesign(Design):
            async def setup(self):
                pass

        await self.expect_success(["Setup successful"], TestDesign())


class DesignSetConfigurationValidationTest(ValidationTest, IsolatedAsyncioTestCase):
    def get_validator(self, design: Design):
        return DesignSetConfigurationValidator(design, status=self.status)

    async def test_set_configuration_is_fine_but_compiler_set_to_none(self):
        class TestDesign(Design):
            async def set_configuration(self):
                self.compiler = None
                pass

        await self.expect_error("**Compiler** is `None`", TestDesign())

    async def test_set_configuration_is_fine_but_no_pass(self):
        class TestDesign(Design):
            async def set_configuration(self):
                self.compiler = mysql.Compiler(quote=quoting.singlequote)
                pass

        await self.expect_error("**Method** is not defined", TestDesign())

    async def test_set_configuration_is_fine_but_method_wrong_type(self):
        class TestDesign(Design):
            async def set_configuration(self):
                self.compiler = mysql.Compiler(quote=quoting.singlequote)
                self.method = 123

        await self.expect_error("`123` is not an instance of **Method**", TestDesign())

    async def test_set_configuration_is_fine_but_no_mapper(self):
        class TestDesign(Design):
            async def set_configuration(self):
                self.compiler = mysql.Compiler(quote=quoting.singlequote)
                self.method = SimpleMethod(self.compiler, None)

        await self.expect_error("**Mapper** is not defined", TestDesign())

    async def test_set_configuration_is_fine_but_mapper_wrong_type(self):
        class TestDesign(Design):
            async def set_configuration(self):
                self.compiler = mysql.Compiler(quote=quoting.singlequote)
                self.method = SimpleMethod(self.compiler, None)
                self.mapper = 123

        await self.expect_error("`123` is not an instance of **Mapper**", TestDesign())

    async def test_set_configuration_raises_exception(self):
        class TestDesign(Design):
            async def set_configuration(self):
                raise ValueError("woops")

        await self.expect_error("Configuration raises an exception", TestDesign())

    async def test_set_configuration_is_fine_and_everything_is_set(self):
        class TestDesign(Design):
            async def set_configuration(self):
                self.compiler = mysql.Compiler(quote=quoting.singlequote)
                self.method = SimpleMethod(self.compiler, None)
                self.mapper = mysql.Mapper(self.method)

        await self.expect_success(
            [
                "Configuration successful",
                "**Compiler** is properly set",
                "**Method** is properly set",
                "**Mapper** is properly set",
            ],
            TestDesign(),
        )
