"""An asynchronous requests Session, to fit with ending's asynchronous design."""

from __future__ import annotations

import urllib3
import urllib3.exceptions

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import requests

# Disable the insecure request warning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

__all__ = ["AsyncSession"]


class AsyncSession(requests.Session):
    """A wrapper around the `Session` class of request that performs requests
    asynchronously.
    Its only difference with requests' class is that `get()`, `post()`, and
    `request()` are asynchronous.

    In addition, `allow_redirects` is disabled by default, but can be set on
    every request using `AsyncSession.allow_redirects`.

    Example:

        Run an asynchronous GET request to google:

            session = AsyncSession()
            await session.get("https://google.com/")

    """

    allow_redirects: bool = False
    """Whether to follow redirects. Defaults to `False`.
    This can be overwritten on a per-request basis using the eponym keyword
    argument.
    """

    def __init__(self, *args, workers: int = 2, loop=None, **kwargs):
        """
        Args:
            workers (int): Number of concurrent workers. Defaults to `2`.
            loop: an asyncio even loop. Defaults to the current loop.
        """
        super().__init__(*args, **kwargs)

        self.loop = loop or asyncio.get_event_loop()
        self.thread_pool = ThreadPoolExecutor(max_workers=workers)

    def request(self, *args, **kwargs) -> asyncio.Future[requests.Response]:
        """Run original request function in a thread."""
        kwargs.setdefault("allow_redirects", self.allow_redirects)
        func = partial(super().request, *args, **kwargs)
        return self.loop.run_in_executor(self.thread_pool, func)

    def close(self) -> None:
        """Closes the session and its thread pool."""
        self.thread_pool.shutdown()
