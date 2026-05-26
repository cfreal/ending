from abc import ABC

__all__ = [
    "LiveStatus",
    "VoidLiveStatus",
]


class LiveStatus(ABC):
    """Helper class to perform live feedback of slow tasks, such as configuration or
    validation.
    """

    def start(self) -> None:
        """Called when the live feedback on the status starts."""

    def done(self) -> None:
        """Called when the live feedback on the status stops."""

    def status(self, message: str) -> None:
        """Called when the status changes."""

    def section(self, name: str, message: str = None) -> None:
        """Called when a new section starts."""

    def info(self, message: str) -> None:
        """Called to forward an info message."""

    def success(self, message: str) -> None:
        """Called to forward a success message."""

    def failure(self, message: str) -> None:
        """Called to forward a failure message."""

    def warning(self, message: str) -> None:
        """Called to forward a warning message."""


class VoidLiveStatus(LiveStatus):
    """A mock LiveStatus class that does nothing."""
