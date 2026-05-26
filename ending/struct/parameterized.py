from __future__ import annotations

from typing import Any, Type, Union

from ending.exception import EndingException

__all__ = ["Parameterized", "ParameterError", "RequiredParameterError"]


class ParameterError(EndingException):
    """The parameter was set to an invalid value."""

    _MESSAGE = "parameter {parameter!r} is invalid"

    def __init__(self, parameter: str, details: str | None = None):
        message = self._MESSAGE
        if details:
            message += ": {details}"

        super().__init__(message.format(parameter=parameter, details=details))


class RequiredParameterError(ParameterError):
    """A parameter is missing."""

    _MESSAGE = "parameter {parameter!r} is required"


class Parameterized:
    def check_parameters(
        self,
        locals: dict[str, Any],
        *,
        required: dict[str, type | tuple[type, ...]],
        optional: dict[str, type | tuple[type, ...]],
    ):
        """Checks that given parameters are specified of the proper type.

        Args:
            locals: the `locals()` of the calling function
            required: allowed type(s) for each required parameter
            optional: allowed type(s) for each optional parameter
        """

        for name, types in required.items():
            self.check_argument_type(name, locals[name], types)

        for name, types in optional.items():
            if locals[name] is not None:
                self.check_argument_type(name, locals[name], types)

    def check_argument_type(
        self, parameter: str, value: Any, types: type | tuple[type]
    ) -> None:
        if not isinstance(value, types):
            if isinstance(types, type):
                stypes = types.__name__
            elif len(types) == 1:
                stypes = types[0].__name__
            else:
                stypes = ", ".join(type.__name__ for type in types)
                stypes = f"one of ({stypes})"
            raise ParameterError(
                parameter, f"type must be {stypes}, not {type(value).__name__}"
            )
