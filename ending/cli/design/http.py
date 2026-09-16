"""HTTP designs: `Design` subclasses that inject over HTTP, each wrapping a different
HTTP client (`requests`, `aiohttp`, `httpx`).

Whatever the client, every response they return carries a `latency` attribute: a `float`
holding the time, in seconds, the request took between leaving and the response arriving.
"""

from __future__ import annotations

import asyncio
import time
from abc import abstractmethod
from types import SimpleNamespace
from typing import Generic, TypeVar

import aiohttp
import httpx
from yarl import URL
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.response import HTTPResponse

from ending.cli.design import Design
from ending.util.requests import AsyncSession

__all__ = [
    "BaseHTTPDesign",
    "HTTPDesign",
    "AIOHTTPDesign",
    "HTTPXHTTPDesign",
]


T = TypeVar("T")


class BaseHTTPDesign(Design, Generic[T]):
    """Base class for HTTP designs.

    Every response returned by the session carries a `latency` attribute: a `float`
    holding the time, in seconds, the request took between leaving and the response
    arriving.
    """

    PROXY: str = None
    """Optional proxy to use, such as `http://localhost:8080`. Defaults to none."""
    WORKERS: int = 2
    """Number of workers (concurrent connections) to use. Defaults to 2."""

    session: T
    """Asynchronous HTTP session."""

    @abstractmethod
    async def create_session(self) -> T:
        """Creates an HTTP session."""
        ...

    @abstractmethod
    async def close_session(self) -> None:
        """Closes the HTTP session."""
        ...

    async def setup(self) -> None:
        """Sets up the design."""
        await super().setup()
        self.session = await self.create_session()

    async def teardown(self) -> None:
        """Tears down the design."""
        await super().teardown()
        await self.close_session()


class _LatencyTimerMixin:
    """Connection mixin measuring the pure request/response wire time.

    The clock starts right before the request bytes leave an already connected
    socket and stops once the response status and headers arrive, so pool wait,
    DNS resolution and the TCP/TLS handshake are all excluded.
    """

    def request(self, *args, **kwargs) -> None:
        self._latency_pending = True
        super().request(*args, **kwargs)

    def send(self, data: bytes) -> None:
        if getattr(self, "_latency_pending", False):
            # Connect first so its setup does not count, then start the clock.
            if self.sock is None:
                self.connect()
            self._latency_start = time.perf_counter()
            self._latency_pending = False
        super().send(data)

    def getresponse(self) -> HTTPResponse:
        response = super().getresponse()
        response.latency = time.perf_counter() - self._latency_start
        return response


class _TimedHTTPConnection(_LatencyTimerMixin, HTTPConnection): ...


class _TimedHTTPSConnection(_LatencyTimerMixin, HTTPSConnection): ...


class _TimedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _TimedHTTPConnection


class _TimedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _TimedHTTPSConnection


class LatencyAdapter(HTTPAdapter):
    """A `requests` adapter that stores each response's wire latency, in seconds, on
    `response.latency` (see `_LatencyTimerMixin` for what is measured)."""

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **kwargs
    ) -> None:
        super().init_poolmanager(connections, maxsize, block, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _TimedHTTPConnectionPool,
            "https": _TimedHTTPSConnectionPool,
        }

    def build_response(self, req: PreparedRequest, resp: HTTPResponse) -> Response:
        response = super().build_response(req, resp)
        response.latency = getattr(resp, "latency", None)
        return response


class HTTPDesign(BaseHTTPDesign[AsyncSession]):
    """A design for web injections.

    This design creates an `AsyncSession` instance to perform HTTP requests. Every
    response carries a `latency` attribute (see `BaseHTTPDesign`).
    """

    session: AsyncSession
    """Asynchronous HTTP session. The session has the same API as `requests.Session`.
    """

    async def create_session(self) -> AsyncSession:
        """Creates an HTTP session."""
        workers = int(self.options.get("workers", self.WORKERS))
        session = AsyncSession(workers=workers)
        session.verify = False
        adapter = LatencyAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        proxy = self.options.get("proxy", self.PROXY)
        if proxy:
            session.proxies = {"all": proxy}
        return session

    async def close_session(self) -> None:
        """Closes the HTTP session."""
        self.session.close()


class _ProxyClientSession(aiohttp.ClientSession):
    """A `ClientSession` that routes every request through a default proxy.

    aiohttp only accepts a proxy per request, so the proxy is injected into each
    request unless one is explicitly passed.
    """

    def __init__(self, *args, proxy: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._default_proxy = proxy

    async def _request(
        self, method: str, str_or_url: str | URL, **kwargs
    ) -> aiohttp.ClientResponse:
        kwargs.setdefault("proxy", self._default_proxy)
        return await super()._request(method, str_or_url, **kwargs)


class AIOHTTPDesign(BaseHTTPDesign[aiohttp.ClientSession]):
    """A design for web injections using aiohttp.

    Every response carries a `latency` attribute (see `BaseHTTPDesign`).
    """

    session: aiohttp.ClientSession
    """Asynchronous HTTP session using aiohttp."""

    async def create_session(self) -> aiohttp.ClientSession:
        workers = int(self.options.get("workers", self.WORKERS))
        connector = aiohttp.TCPConnector(limit=workers, ssl=False)
        proxy = self.options.get("proxy", self.PROXY)
        session = _ProxyClientSession(
            connector=connector,
            trace_configs=[self._create_trace_config()],
            proxy=proxy,
        )
        return session

    async def close_session(self) -> None:
        await self.session.close()

    @staticmethod
    async def _on_request_headers_sent(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: aiohttp.TraceRequestHeadersSentParams,
    ) -> None:
        # Fires once the request headers hit the socket, i.e. after the connection
        # has been established, so connection setup is excluded from the latency.
        context.start = asyncio.get_event_loop().time()

    @staticmethod
    async def _on_request_end(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        params.response.latency = asyncio.get_event_loop().time() - context.start

    def _create_trace_config(self) -> aiohttp.TraceConfig:
        """Builds a trace config that records each response's latency."""
        trace_config = aiohttp.TraceConfig()
        trace_config.on_request_headers_sent.append(self._on_request_headers_sent)
        trace_config.on_request_end.append(self._on_request_end)
        return trace_config


class HTTPXHTTPDesign(BaseHTTPDesign[httpx.AsyncClient]):
    """A design for web injections using httpx.

    Every response carries a `latency` attribute (see `BaseHTTPDesign`).
    """

    session: httpx.AsyncClient
    """Asynchronous HTTP session using httpx."""

    @staticmethod
    async def _on_request(request: httpx.Request) -> None:
        # httpcore reports fine-grained events; timing the request headers going
        # out to the response headers coming in excludes connection setup.
        start: list[float] = []
        end: list[float] = []

        async def trace(name: str, info: dict) -> None:
            if name == "http11.send_request_headers.started":
                start.append(time.perf_counter())
            elif name == "http11.receive_response_headers.complete":
                end.append(time.perf_counter())

        request.extensions = {**request.extensions, "trace": trace}
        request._ending_latency = (start, end)

    @staticmethod
    async def _on_response(response: httpx.Response) -> None:
        start, end = response.request._ending_latency
        response.latency = (end[0] - start[0]) if start and end else None

    async def create_session(self) -> httpx.AsyncClient:
        workers = int(self.options.get("workers", self.WORKERS))
        proxy = self.options.get("proxy", self.PROXY)
        session = httpx.AsyncClient(
            verify=False,
            proxy=proxy,
            limits=httpx.Limits(max_connections=workers),
            event_hooks={
                "request": [self._on_request],
                "response": [self._on_response],
            },
        )
        return session

    async def close_session(self) -> None:
        await self.session.aclose()
