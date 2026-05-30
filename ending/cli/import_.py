"""Converts a raw HTTP request (stdin) into a new ending design."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from email.parser import BytesParser
from email.policy import HTTP
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from rich.rule import Rule

from ending.cli.design import DesignDirectory, DesignEditor
from ending.cli.misc import PFX_ERROR, PFX_INFO, PFX_SUCCESS, console, message_success

__all__ = ["do_import"]


# Headers stripped from the generated output. Compared case-insensitively,
# but the original casing of kept headers is preserved.
REMOVED_HEADERS: tuple[str, ...] = (
    "host",  # implied by the URL
    "content-length",  # requests sets this automatically
    "cookie",  # passed separately via cookies=
    "connection",
)


MultipartFileValue = tuple[str, str, str]
MultipartFieldValue = tuple[None, str]
MultipartValue = MultipartFileValue | MultipartFieldValue
MultipartPart = tuple[str, bool, MultipartValue]


class HttpRequest:
    """A parsed HTTP request: method, target, headers and raw body."""

    def __init__(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> None:
        self.method = method
        self.target = target
        self.headers = headers
        self.body = body

    @classmethod
    def from_url(cls, url: str) -> "HttpRequest":
        """Builds a GET HttpRequest from a bare URL (no body, no extra headers)."""
        host = urlsplit(url).netloc
        return cls("GET", url, [("Host", host)], b"")

    @classmethod
    def parse(cls, raw: bytes | bytearray) -> "HttpRequest":
        """Builds an HttpRequest from raw bytes."""
        if not isinstance(raw, (bytes, bytearray)):
            raise TypeError("raw must be bytes")
        raw = bytes(raw)

        first_line, headers_blob, body = cls._split(raw)

        parts = first_line.decode("latin-1").split()
        if len(parts) != 3 or not parts[2].upper().startswith("HTTP/"):
            raise ValueError(f"malformed request line: {first_line!r}")
        method, target = parts[0], parts[1]
        if not method.isalpha():
            raise ValueError(f"malformed HTTP method: {method!r}")

        headers = cls._parse_headers(headers_blob)
        return cls(method, target, headers, body)

    @staticmethod
    def _split(raw: bytes) -> tuple[bytes, bytes, bytes]:
        """Splits raw bytes into (request_line, headers_blob, body)."""
        head, _, body = raw.partition(b"\r\n\r\n")
        if not _:
            head, _, body = raw.partition(b"\n\n")

        first_line, _, headers_blob = head.partition(b"\r\n")
        if not headers_blob and b"\n" in head:
            first_line, _, headers_blob = head.partition(b"\n")

        return first_line, headers_blob, body

    @staticmethod
    def _parse_headers(headers_blob: bytes) -> list[tuple[str, str]]:
        """Parses a header block into a list of (name, value), preserving
        original text verbatim."""
        headers = []
        text = headers_blob.decode("latin-1")
        for line in text.split("\n"):
            line = line.rstrip("\r")
            if not line:
                continue
            name, sep, value = line.partition(":")
            if not sep:
                continue
            headers.append((name.strip(), value.strip()))
        return headers

    def header(self, name: str) -> str:
        """Returns the last value of a header (case-insensitive), or ''."""
        wanted = name.lower()
        found = ""
        for key, value in self.headers:
            if key.lower() == wanted:
                found = value
        return found

    def kept_headers(self) -> dict[str, str]:
        """Returns the headers dict minus REMOVED_HEADERS, original casing kept."""
        removed = {h.lower() for h in REMOVED_HEADERS}
        return {k: v for k, v in self.headers if k.lower() not in removed}

    def content_type(self) -> str:
        """Returns the lowercased media type without parameters."""
        return self.header("content-type").split(";", 1)[0].strip().lower()

    def cookies(self) -> dict[str, str]:
        """Parses the Cookie header into a {name: value} dict (empty if none)."""
        if not (raw := self.header("cookie")):
            return {}
        jar = SimpleCookie()
        jar.load(raw)
        return {name: morsel.value for name, morsel in jar.items()}

    def query_params(self) -> dict[str, str]:
        """Parses the target's query string into a {name: value} dict."""
        query = urlsplit(self.target).query
        return dict(parse_qsl(query)) if query else {}

    def url(self) -> str:
        """Builds an absolute URL from the target and Host header, without
        the query string (that goes into params=)."""
        if self.target.startswith("http://") or self.target.startswith("https://"):
            base = self.target
        else:
            base = "http://" + self.header("host") + self.target
        scheme, netloc, path, _query, fragment = urlsplit(base)
        return urlunsplit((scheme, netloc, path, "", fragment))


class RequestBody:
    """Base class for encoding a request body into requests kwargs."""

    MEDIA_TYPE: str | None = None

    _registry: list[type["RequestBody"]] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.MEDIA_TYPE is not None:
            RequestBody._registry.append(cls)

    @classmethod
    def for_request(cls, request: HttpRequest) -> "RequestBody":
        """Picks the RequestBody subclass matching the request's content type."""
        ctype = request.content_type()
        for subclass in cls._registry:
            if subclass.MEDIA_TYPE == ctype:
                return subclass(request)
        return RawBody(request)

    def __init__(self, request: HttpRequest) -> None:
        self.request = request
        self.body = request.body

    def kwargs(self) -> dict[str, str]:
        raise NotImplementedError


class RawBody(RequestBody):
    """Fallback: passes the body through verbatim as data=."""

    def kwargs(self) -> dict[str, str]:
        return {"data": repr(self.body.decode("latin-1"))}


class JsonBody(RequestBody):
    """application/json -> json= with the parsed object."""

    MEDIA_TYPE = "application/json"

    def kwargs(self) -> dict[str, str]:
        try:
            return {"json": repr(json.loads(self.body))}
        except ValueError:
            return RawBody(self.request).kwargs()


class UrlEncodedBody(RequestBody):
    """application/x-www-form-urlencoded -> data= dict."""

    MEDIA_TYPE = "application/x-www-form-urlencoded"

    def kwargs(self) -> dict[str, str]:
        form = dict(parse_qsl(self.body.decode("latin-1").removesuffix("\n")))
        return {"data": repr(form)}


class MultipartBody(RequestBody):
    """multipart/form-data, rendered to keep both readability and order."""

    MEDIA_TYPE = "multipart/form-data"

    def kwargs(self) -> dict[str, str]:
        if not (parts := self._parse()):
            return {}

        if self._is_interleaved(parts):
            unified = {name: value for name, _is_file, value in parts}
            return {"files": repr(unified)}

        form_fields = {n: v for n, is_file, v in parts if not is_file}
        file_fields = {n: v for n, is_file, v in parts if is_file}
        result = {}
        if form_fields:
            result["data"] = repr({n: v[1] for n, v in form_fields.items()})
        if file_fields:
            result["files"] = repr(file_fields)
        return result

    @staticmethod
    def _is_interleaved(parts: list[MultipartPart]) -> bool:
        seen_file = False
        for _name, is_file, _value in parts:
            if is_file:
                seen_file = True
            elif seen_file:
                return True
        return False

    def _parse(self) -> list[MultipartPart]:
        header = self.request.header("content-type")
        blob = b"Content-Type: " + header.encode() + b"\r\n\r\n" + self.body
        msg = BytesParser(policy=HTTP).parsebytes(blob)

        parts = []
        for part in msg.iter_parts():
            name_raw = part.get_param("name", header="content-disposition")
            if name_raw is None:
                continue
            name = name_raw if isinstance(name_raw, str) else str(name_raw)

            filename_raw = part.get_param("filename", header="content-disposition")
            filename = None
            if filename_raw is not None:
                filename = (
                    filename_raw if isinstance(filename_raw, str) else str(filename_raw)
                )

            raw_payload = part.get_content()
            if isinstance(raw_payload, bytes):
                payload = raw_payload.decode("latin-1")
            else:
                payload = str(raw_payload)

            if filename is not None:
                file_value = (filename, payload, part.get_content_type())
                parts.append((name, True, file_value))
            else:
                field_value = (None, payload)
                parts.append((name, False, field_value))

        return parts


class CodeGenerator:
    """Renders an HttpRequest as an async def send() method."""

    def generate(self, request: HttpRequest) -> str:
        """Returns the full async def send() method for the given request."""
        lines = self._generate_call(request).splitlines()
        result = ["async def send(self, payload: str) -> bytes:"]
        result.append("    response = await " + lines[0])
        for line in lines[1:]:
            result.append("    " + line)
        result.append("    return response.content")
        return "\n".join(result)

    def _generate_call(self, request: HttpRequest) -> str:
        """Returns the bare self.session.<method>(...) call expression."""
        kwargs = {}

        if params := request.query_params():
            kwargs["params"] = self._pretty_dict(params)

        if kept := request.kept_headers():
            kwargs["headers"] = self._pretty_dict(kept)

        if cookies := request.cookies():
            kwargs["cookies"] = self._pretty_dict(cookies)

        if request.body:
            kwargs.update(RequestBody.for_request(request).kwargs())

        call = f"self.session.{request.method.lower()}("
        first_arg = f"\n    {request.url()!r},"
        if kwargs:
            return call + first_arg + "\n" + self._format(kwargs) + "\n)"
        return call + first_arg + "\n)"

    @staticmethod
    def _format(kwargs: dict[str, str]) -> str:
        return "\n".join(f"    {name}={value}," for name, value in kwargs.items())

    @staticmethod
    def _pretty_dict(value: dict[str, str]) -> str:
        items = [f"{k!r}: {v!r}" for k, v in value.items()]
        body = "\n".join(f"        {item}," for item in items)
        return "{\n" + body + "\n    }"


def _parse_input(raw: bytes) -> HttpRequest:
    """Parse raw stdin: a bare URL (http/https) or a full HTTP request."""
    stripped = raw.strip()
    if stripped.startswith(b"http://") or stripped.startswith(b"https://"):
        return HttpRequest.from_url(stripped.decode("ascii"))
    return HttpRequest.parse(raw)


def http_request_to_code(raw: bytes) -> str:
    """Converts raw bytes (URL or HTTP request) into an async def send() method."""
    return CodeGenerator().generate(_parse_input(raw))


async def do_import(design_dir: DesignDirectory, namespace: Namespace) -> None:
    if design_dir.exists():
        if not namespace.force:
            console.print(
                f"{PFX_ERROR} Design [b]{design_dir.name}[/] already exists. "
                "Use [i]--force[/i] to overwrite."
            )
            return
        design_dir.remove()

    if sys.stdin.isatty():
        rule = Rule(style="import-rule")
        console.print()
        under = "[import-rule]↓↓↓[/import-rule]"
        console.print(
            f"{under} Paste your HTTP request or URL below, then press [b]Ctrl-D[/b] {under}"
        )
        console.print(rule)

    raw = sys.stdin.buffer.read()

    if sys.stdin.isatty():
        console.print(rule)
        console.print()

    try:
        request = _parse_input(raw)
    except (ValueError, TypeError) as e:
        console.print(f"{PFX_ERROR} Failed to parse HTTP request: {e}")
        return

    send_code = CodeGenerator().generate(request)

    design_dir.create()

    design_class = design_dir.load()
    design = design_class()
    editor = DesignEditor(design)
    editor.set_method("send", send_code)

    console.print(
        f"{PFX_SUCCESS} Design [b]{design_dir.name}[/] created in "
        f"[i]{design_dir.get_module_path()}[/i]"
    )
    message_success("IMPORTED")

    from ending.cli.parse import do_edit

    await do_edit(design_dir, namespace)
