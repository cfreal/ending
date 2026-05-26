"""Ending exceptions."""

from typing import _GenericAlias


class EndingException(Exception):
    """Base class for ending exceptions."""


class CompilerNotSetError(EndingException):
    """Node's `__compiler__` object is empty."""

    def __init__(self):
        super().__init__("Node.__compiler__ is not set")


class NodeTypeError(TypeError, EndingException):
    """The creation of a `Node` fails due to a type error.

    For instance, creating an `ending.ast.Identifier` with an array:

        >>> Identifier([1, 2, 3])
        NodeTypeError: Identifier.name: expected str, got: [1, 2, 3]
    """

    def __init__(self, node, field_name: str, field_type: type | str, value):
        try:
            field_type = field_type.__name__
        except AttributeError:
            pass

        if isinstance(value, type) or isinstance(value, _GenericAlias):
            got = value.__name__
        else:
            got = f"{value!r} ({type(value).__name__})"

        if not isinstance(node, type):
            node = type(node)

        super().__init__(
            f"{node.__name__}.{field_name}: " f"expected {field_type}, got {got}"
        )


class CompilationError(EndingException):
    """An AST cannot be compiled."""


class InjectionError(EndingException):
    """Error during the injection."""

    def __init__(self, message: str, payload=None):
        super().__init__(message)
        self.message = message
        self.payload = payload

    def __str__(self):
        message = str(self.message)

        if self.payload:
            try:
                payload = str(self.payload)
            except:
                payload = self.payload

            message += f" [Payload: {payload!r}]"

        return message


class ConversionError(InjectionError):
    """Error during the conversion of a value.

    This generally happens on `Compiler.serialize` or `Compiler.unserialize`.
    """
