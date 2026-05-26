from typing import Any

__all__ = ["Freezable"]


# NOTE There are faster implementations of this, but this one is the most readable and
# does not penalize us too much in terms of performance.
class Freezable:
    """A class whose attributes can be frozen, i.e. made immutable, and unfrozen."""

    __frozen: bool = False

    def _freeze(self) -> None:
        if not self.__frozen:
            self.__frozen = True

    def _unfreeze(self) -> None:
        if self.__frozen:
            object.__setattr__(self, "_Freezable__frozen", False)

    def __setattr__(self, name: str, value: Any) -> None:
        if self.__frozen:
            raise AttributeError(
                f"{type(self).__name__}: Cannot set attribute {name!r}, object is frozen"
            )
        super().__setattr__(name, value)
