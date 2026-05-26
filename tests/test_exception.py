import unittest

from ending.ast import *
from ending.exception import *
from ending.util.humanized_compiler import HumanizedCompiler


class TestException(unittest.TestCase):
    def test_InjectionError_str_payload(self):
        self.assertEqual(
            str(InjectionError(message="test123", payload="payload123")),
            "test123 [Payload: 'payload123']",
        )

    def test_InjectionError_no_payload(self):
        self.assertEqual(str(InjectionError(message="test123")), "test123")

    def test_InjectionError_node_payload_no_compiler(self):
        self.maxDiff = None
        payload = Query("users").columns("a", "b")
        self.assertEqual(
            str(InjectionError(message="test123", payload=payload)),
            "test123 [Payload: Query(q=QueryParts(table=Identifier(name='users'), columns=List[Identifier]((Identifier(name='a'), Identifier(name='b'))), distinct=False, where=None, order=None, limit=None))]",
        )

    def test_InjectionError_node_payload_with_compiler(self):
        payload = Query("users").columns("a", "b")
        HumanizedCompiler().wrap(payload)

        self.assertEqual(
            str(InjectionError(message="test123", payload=payload)),
            "test123 [Payload: 'SELECT a, b FROM users']",
        )
