import unittest

from ending.cli.import_ import (
    CodeGenerator,
    HttpRequest,
    JsonBody,
    MultipartBody,
    RawBody,
    RequestBody,
    UrlEncodedBody,
    _parse_input,
    http_request_to_code,
)


def make_request(
    method="GET",
    target="/",
    headers=(),
    body=b"",
) -> HttpRequest:
    """Build an HttpRequest directly without going through parse()."""
    return HttpRequest(method, target, list(headers), body)


# ---------------------------------------------------------------------------
# HttpRequest.parse() — request line
# ---------------------------------------------------------------------------


class TestHttpRequestParseLine(unittest.TestCase):
    def _parse(self, raw: bytes) -> HttpRequest:
        return HttpRequest.parse(raw)

    def test_basic_get(self):
        r = self._parse(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertEqual(r.method, "GET")
        self.assertEqual(r.target, "/")

    def test_basic_post(self):
        r = self._parse(b"POST /submit HTTP/1.1\r\nHost: x\r\n\r\nbody")
        self.assertEqual(r.method, "POST")
        self.assertEqual(r.target, "/submit")
        self.assertEqual(r.body, b"body")

    def test_method_case_preserved(self):
        r = self._parse(b"delete /res HTTP/1.1\r\n\r\n")
        self.assertEqual(r.method, "delete")

    def test_target_with_query_string(self):
        r = self._parse(b"GET /search?q=test&page=2 HTTP/1.1\r\n\r\n")
        self.assertEqual(r.target, "/search?q=test&page=2")

    def test_absolute_form_target(self):
        r = self._parse(b"GET http://example.com/path HTTP/1.1\r\n\r\n")
        self.assertEqual(r.target, "http://example.com/path")

    def test_non_bytes_raises_type_error(self):
        with self.assertRaises(TypeError):
            HttpRequest.parse("GET / HTTP/1.1\r\n\r\n")

    def test_bytearray_is_accepted(self):
        raw = bytearray(b"GET / HTTP/1.1\r\n\r\n")
        r = HttpRequest.parse(raw)
        self.assertEqual(r.method, "GET")

    def test_too_few_parts_raises_value_error(self):
        with self.assertRaises(ValueError):
            HttpRequest.parse(b"GET /\r\n\r\n")

    def test_missing_http_version_raises_value_error(self):
        with self.assertRaises(ValueError):
            HttpRequest.parse(b"GET / NOTHTTP\r\n\r\n")

    def test_method_with_non_alpha_raises_value_error(self):
        with self.assertRaises(ValueError):
            HttpRequest.parse(b"GET1 / HTTP/1.1\r\n\r\n")

    def test_http2_version_accepted(self):
        r = HttpRequest.parse(b"GET / HTTP/2\r\n\r\n")
        self.assertEqual(r.method, "GET")

    def test_version_check_is_case_insensitive(self):
        r = HttpRequest.parse(b"GET / http/1.1\r\n\r\n")
        self.assertEqual(r.method, "GET")


# ---------------------------------------------------------------------------
# HttpRequest._split() — CRLF vs LF line endings
# ---------------------------------------------------------------------------


class TestHttpRequestSplit(unittest.TestCase):
    def test_crlf_separates_headers_from_body(self):
        raw = b"GET / HTTP/1.1\r\nHost: x\r\n\r\nbody"
        r = HttpRequest.parse(raw)
        self.assertEqual(r.body, b"body")

    def test_lf_only_line_endings_fallback(self):
        raw = b"GET / HTTP/1.1\nHost: x\n\nbody"
        r = HttpRequest.parse(raw)
        self.assertEqual(r.method, "GET")
        self.assertEqual(r.body, b"body")

    def test_empty_body(self):
        r = HttpRequest.parse(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual(r.body, b"")

    def test_body_with_crlf_preserved(self):
        raw = b"POST / HTTP/1.1\r\n\r\nline1\r\nline2"
        r = HttpRequest.parse(raw)
        self.assertEqual(r.body, b"line1\r\nline2")

    def test_binary_body_preserved(self):
        body = bytes(range(256))
        raw = b"POST / HTTP/1.1\r\n\r\n" + body
        r = HttpRequest.parse(raw)
        self.assertEqual(r.body, body)


# ---------------------------------------------------------------------------
# HttpRequest._parse_headers() — header parsing
# ---------------------------------------------------------------------------


class TestHttpRequestParseHeaders(unittest.TestCase):
    def _parse(self, raw: bytes) -> HttpRequest:
        return HttpRequest.parse(raw)

    def test_single_header(self):
        r = self._parse(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertIn(("Host", "example.com"), r.headers)

    def test_header_value_trimmed(self):
        r = self._parse(b"GET / HTTP/1.1\r\nX-Foo:   bar   \r\n\r\n")
        self.assertIn(("X-Foo", "bar"), r.headers)

    def test_header_name_casing_preserved(self):
        r = self._parse(b"GET / HTTP/1.1\r\nX-Custom-Header: val\r\n\r\n")
        names = [name for name, _ in r.headers]
        self.assertIn("X-Custom-Header", names)

    def test_colon_in_value_preserved(self):
        r = self._parse(b"GET / HTTP/1.1\r\nAuthorization: Bearer abc:def\r\n\r\n")
        self.assertEqual(r.header("Authorization"), "Bearer abc:def")

    def test_multiple_headers(self):
        raw = (
            b"GET / HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"Accept: text/html\r\n"
            b"X-Foo: bar\r\n"
            b"\r\n"
        )
        r = self._parse(raw)
        self.assertEqual(len(r.headers), 3)

    def test_malformed_header_without_colon_skipped(self):
        raw = b"GET / HTTP/1.1\r\nNotAHeader\r\nValid: yes\r\n\r\n"
        r = self._parse(raw)
        names = [name for name, _ in r.headers]
        self.assertNotIn("NotAHeader", names)
        self.assertIn("Valid", names)

    def test_no_headers(self):
        r = self._parse(b"GET / HTTP/1.1\r\n\r\n")
        self.assertEqual(r.headers, [])

    def test_lf_only_header_block(self):
        raw = b"GET / HTTP/1.1\nHost: example.com\nX-A: B\n\n"
        r = self._parse(raw)
        self.assertEqual(r.header("Host"), "example.com")
        self.assertEqual(r.header("X-A"), "B")

    def test_mixed_crlf_within_headers(self):
        raw = b"GET / HTTP/1.1\r\nHost: example.com\r\nX-A: B\r\n\r\n"
        r = self._parse(raw)
        self.assertEqual(r.header("X-A"), "B")


# ---------------------------------------------------------------------------
# HttpRequest.header()
# ---------------------------------------------------------------------------


class TestHttpRequestHeader(unittest.TestCase):
    def setUp(self):
        self.req = make_request(
            headers=[
                ("Content-Type", "application/json"),
                ("X-Foo", "first"),
                ("X-FOO", "second"),
            ]
        )

    def test_case_insensitive_lookup(self):
        self.assertEqual(self.req.header("content-type"), "application/json")
        self.assertEqual(self.req.header("CONTENT-TYPE"), "application/json")
        self.assertEqual(self.req.header("Content-Type"), "application/json")

    def test_last_value_wins_on_duplicates(self):
        self.assertEqual(self.req.header("x-foo"), "second")

    def test_missing_header_returns_empty_string(self):
        self.assertEqual(self.req.header("X-Missing"), "")


# ---------------------------------------------------------------------------
# HttpRequest.kept_headers()
# ---------------------------------------------------------------------------


class TestHttpRequestKeptHeaders(unittest.TestCase):
    def test_host_removed(self):
        req = make_request(headers=[("Host", "example.com"), ("X-Keep", "yes")])
        kept = req.kept_headers()
        self.assertNotIn("Host", kept)
        self.assertIn("X-Keep", kept)

    def test_content_length_removed(self):
        req = make_request(headers=[("Content-Length", "42"), ("X-Keep", "yes")])
        self.assertNotIn("Content-Length", req.kept_headers())

    def test_cookie_removed(self):
        req = make_request(headers=[("Cookie", "session=abc"), ("X-Keep", "yes")])
        self.assertNotIn("Cookie", req.kept_headers())

    def test_removal_is_case_insensitive(self):
        req = make_request(
            headers=[
                ("HOST", "example.com"),
                ("content-length", "0"),
                ("COOKIE", "a=b"),
                ("X-Keep", "yes"),
            ]
        )
        kept = req.kept_headers()
        self.assertEqual(list(kept.keys()), ["X-Keep"])

    def test_original_casing_preserved(self):
        req = make_request(headers=[("X-Custom-Header", "value")])
        kept = req.kept_headers()
        self.assertIn("X-Custom-Header", kept)

    def test_empty_when_all_removed(self):
        req = make_request(
            headers=[
                ("Host", "x"),
                ("Content-Length", "0"),
                ("Cookie", "a=1"),
            ]
        )
        self.assertEqual(req.kept_headers(), {})

    def test_duplicate_kept_headers_collapse_to_last(self):
        req = make_request(headers=[("X-Foo", "first"), ("X-Foo", "second")])
        kept = req.kept_headers()
        self.assertEqual(kept["X-Foo"], "second")


# ---------------------------------------------------------------------------
# HttpRequest.content_type()
# ---------------------------------------------------------------------------


class TestHttpRequestContentType(unittest.TestCase):
    def test_simple_content_type(self):
        req = make_request(headers=[("Content-Type", "application/json")])
        self.assertEqual(req.content_type(), "application/json")

    def test_strips_parameters(self):
        req = make_request(
            headers=[("Content-Type", "application/json; charset=utf-8")]
        )
        self.assertEqual(req.content_type(), "application/json")

    def test_lowercased(self):
        req = make_request(headers=[("Content-Type", "Application/JSON")])
        self.assertEqual(req.content_type(), "application/json")

    def test_no_content_type_returns_empty(self):
        req = make_request()
        self.assertEqual(req.content_type(), "")

    def test_strips_whitespace_around_media_type(self):
        req = make_request(headers=[("Content-Type", "  text/plain  ; charset=utf-8")])
        self.assertEqual(req.content_type(), "text/plain")


# ---------------------------------------------------------------------------
# HttpRequest.cookies()
# ---------------------------------------------------------------------------


class TestHttpRequestCookies(unittest.TestCase):
    def test_single_cookie(self):
        req = make_request(headers=[("Cookie", "session=abc123")])
        self.assertEqual(req.cookies(), {"session": "abc123"})

    def test_multiple_cookies(self):
        req = make_request(headers=[("Cookie", "a=1; b=2; c=three")])
        self.assertEqual(req.cookies(), {"a": "1", "b": "2", "c": "three"})

    def test_no_cookie_header(self):
        req = make_request()
        self.assertEqual(req.cookies(), {})

    def test_cookie_with_equals_in_value(self):
        req = make_request(headers=[("Cookie", "token=abc=def")])
        self.assertEqual(req.cookies()["token"], "abc=def")


# ---------------------------------------------------------------------------
# HttpRequest.query_params()
# ---------------------------------------------------------------------------


class TestHttpRequestQueryParams(unittest.TestCase):
    def test_single_param(self):
        req = make_request(target="/search?q=hello")
        self.assertEqual(req.query_params(), {"q": "hello"})

    def test_multiple_params(self):
        req = make_request(target="/search?a=1&b=2&c=three")
        self.assertEqual(req.query_params(), {"a": "1", "b": "2", "c": "three"})

    def test_no_query_string(self):
        req = make_request(target="/path")
        self.assertEqual(req.query_params(), {})

    def test_duplicate_params_last_wins(self):
        req = make_request(target="/path?x=1&x=2")
        self.assertEqual(req.query_params()["x"], "2")

    def test_url_encoded_values_decoded(self):
        req = make_request(target="/path?q=hello+world&x=a%3Db")
        params = req.query_params()
        self.assertEqual(params["q"], "hello world")
        self.assertEqual(params["x"], "a=b")

    def test_empty_query_string(self):
        req = make_request(target="/path?")
        self.assertEqual(req.query_params(), {})


# ---------------------------------------------------------------------------
# HttpRequest.url()
# ---------------------------------------------------------------------------


class TestHttpRequestUrl(unittest.TestCase):
    def test_origin_form_uses_host_header(self):
        req = make_request(
            target="/path",
            headers=[("Host", "example.com")],
        )
        self.assertEqual(req.url(), "http://example.com/path")

    def test_query_string_stripped(self):
        req = make_request(
            target="/search?q=test",
            headers=[("Host", "example.com")],
        )
        self.assertEqual(req.url(), "http://example.com/search")

    def test_absolute_form_http(self):
        req = make_request(target="http://example.com/path?q=1")
        self.assertEqual(req.url(), "http://example.com/path")

    def test_absolute_form_https(self):
        req = make_request(target="https://secure.example.com/api?x=1")
        self.assertEqual(req.url(), "https://secure.example.com/api")

    def test_root_path(self):
        req = make_request(target="/", headers=[("Host", "example.com")])
        self.assertEqual(req.url(), "http://example.com/")

    def test_host_with_port(self):
        req = make_request(
            target="/api",
            headers=[("Host", "example.com:8080")],
        )
        self.assertEqual(req.url(), "http://example.com:8080/api")

    def test_fragment_preserved(self):
        req = make_request(target="http://example.com/path#section")
        self.assertEqual(req.url(), "http://example.com/path#section")


# ---------------------------------------------------------------------------
# RequestBody.for_request() dispatch
# ---------------------------------------------------------------------------


class TestRequestBodyDispatch(unittest.TestCase):
    def _req(self, content_type: str, body: bytes = b"{}") -> HttpRequest:
        return make_request(
            method="POST",
            headers=[("Content-Type", content_type)],
            body=body,
        )

    def test_json_dispatches_to_json_body(self):
        body = RequestBody.for_request(self._req("application/json"))
        self.assertIsInstance(body, JsonBody)

    def test_urlencoded_dispatches_to_url_encoded_body(self):
        body = RequestBody.for_request(
            self._req("application/x-www-form-urlencoded", b"a=1")
        )
        self.assertIsInstance(body, UrlEncodedBody)

    def test_multipart_dispatches_to_multipart_body(self):
        ct = "multipart/form-data; boundary=----boundary"
        body = RequestBody.for_request(self._req(ct, b""))
        self.assertIsInstance(body, MultipartBody)

    def test_unknown_type_dispatches_to_raw_body(self):
        body = RequestBody.for_request(self._req("application/octet-stream", b"\x00"))
        self.assertIsInstance(body, RawBody)

    def test_no_content_type_dispatches_to_raw_body(self):
        req = make_request(method="POST", body=b"hello")
        body = RequestBody.for_request(req)
        self.assertIsInstance(body, RawBody)


# ---------------------------------------------------------------------------
# RawBody
# ---------------------------------------------------------------------------


class TestRawBody(unittest.TestCase):
    def _body(self, data: bytes) -> RawBody:
        req = make_request(method="POST", body=data)
        return RawBody(req)

    def test_data_kwarg_present(self):
        self.assertIn("data", self._body(b"hello").kwargs())

    def test_value_is_repr(self):
        kwargs = self._body(b"hello").kwargs()
        self.assertEqual(kwargs["data"], repr("hello"))

    def test_binary_data_uses_latin1(self):
        kwargs = self._body(b"\xff\xfe").kwargs()
        self.assertIn("data", kwargs)
        self.assertEqual(eval(kwargs["data"]).encode("latin-1"), b"\xff\xfe")


# ---------------------------------------------------------------------------
# JsonBody
# ---------------------------------------------------------------------------


class TestJsonBody(unittest.TestCase):
    def _body(self, data: bytes) -> JsonBody:
        req = make_request(
            method="POST",
            headers=[("Content-Type", "application/json")],
            body=data,
        )
        return JsonBody(req)

    def test_json_kwarg_present(self):
        self.assertIn("json", self._body(b'{"key": "val"}').kwargs())

    def test_value_is_repr_of_parsed_object(self):
        kwargs = self._body(b'{"a": 1}').kwargs()
        self.assertEqual(eval(kwargs["json"]), {"a": 1})

    def test_json_array(self):
        kwargs = self._body(b"[1, 2, 3]").kwargs()
        self.assertEqual(eval(kwargs["json"]), [1, 2, 3])

    def test_invalid_json_falls_back_to_data(self):
        kwargs = self._body(b"not json {").kwargs()
        self.assertIn("data", kwargs)
        self.assertNotIn("json", kwargs)

    def test_json_with_unicode(self):
        kwargs = self._body('{"name": "héllo"}'.encode()).kwargs()
        self.assertEqual(eval(kwargs["json"]), {"name": "héllo"})


# ---------------------------------------------------------------------------
# UrlEncodedBody
# ---------------------------------------------------------------------------


class TestUrlEncodedBody(unittest.TestCase):
    def _body(self, data: bytes) -> UrlEncodedBody:
        req = make_request(
            method="POST",
            headers=[("Content-Type", "application/x-www-form-urlencoded")],
            body=data,
        )
        return UrlEncodedBody(req)

    def test_data_kwarg_present(self):
        self.assertIn("data", self._body(b"a=1").kwargs())

    def test_single_field(self):
        kwargs = self._body(b"username=bob").kwargs()
        self.assertEqual(eval(kwargs["data"]), {"username": "bob"})

    def test_multiple_fields(self):
        kwargs = self._body(b"a=1&b=two&c=3").kwargs()
        self.assertEqual(eval(kwargs["data"]), {"a": "1", "b": "two", "c": "3"})

    def test_url_encoded_values_decoded(self):
        kwargs = self._body(b"q=hello+world&x=a%3Db").kwargs()
        data = eval(kwargs["data"])
        self.assertEqual(data["q"], "hello world")
        self.assertEqual(data["x"], "a=b")


# ---------------------------------------------------------------------------
# MultipartBody._is_interleaved()
# ---------------------------------------------------------------------------


class TestMultipartIsInterleaved(unittest.TestCase):
    def _parts(self, pattern: str) -> list:
        """Build a fake parts list from a string like 'ffFFf' (f=field, F=file)."""
        parts = []
        for ch in pattern:
            is_file = ch.isupper()
            value = ("fn", "content", "text/plain") if is_file else (None, "content")
            parts.append(("name", is_file, value))
        return parts

    def test_fields_only_not_interleaved(self):
        self.assertFalse(MultipartBody._is_interleaved(self._parts("fff")))

    def test_files_only_not_interleaved(self):
        self.assertFalse(MultipartBody._is_interleaved(self._parts("FFF")))

    def test_fields_then_files_not_interleaved(self):
        self.assertFalse(MultipartBody._is_interleaved(self._parts("ffFFF")))

    def test_file_before_field_is_interleaved(self):
        self.assertTrue(MultipartBody._is_interleaved(self._parts("Ff")))

    def test_file_between_fields_is_interleaved(self):
        self.assertTrue(MultipartBody._is_interleaved(self._parts("fFf")))

    def test_empty_not_interleaved(self):
        self.assertFalse(MultipartBody._is_interleaved([]))


# ---------------------------------------------------------------------------
# MultipartBody.kwargs() — full parsing
# ---------------------------------------------------------------------------


_BOUNDARY = "----TestBoundary"


def _multipart(fields: dict, files: dict = {}) -> tuple[str, bytes]:
    """Build a Content-Type header and multipart body for testing.

    fields: {name: value}
    files:  {name: (filename, content, content_type)}
    """
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n"
        )
    for name, (filename, content, ctype) in files.items():
        parts.append(
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n"
            f"\r\n"
            f"{content}\r\n"
        )
    body = "".join(parts) + f"--{_BOUNDARY}--\r\n"
    ct = f"multipart/form-data; boundary={_BOUNDARY}"
    return ct, body.encode()


class TestMultipartBodyKwargs(unittest.TestCase):
    def _req(self, ct: str, body: bytes) -> MultipartBody:
        req = make_request(
            method="POST",
            headers=[("Content-Type", ct)],
            body=body,
        )
        return MultipartBody(req)

    def test_fields_only_produces_data(self):
        ct, body = _multipart({"user": "alice", "pass": "secret"})
        kwargs = self._req(ct, body).kwargs()
        self.assertIn("data", kwargs)
        self.assertNotIn("files", kwargs)
        data = eval(kwargs["data"])
        self.assertEqual(data, {"user": "alice", "pass": "secret"})

    def test_files_only_produces_files(self):
        ct, body = _multipart({}, {"upload": ("img.png", "PNGDATA", "image/png")})
        kwargs = self._req(ct, body).kwargs()
        self.assertIn("files", kwargs)
        self.assertNotIn("data", kwargs)

    def test_fields_then_files_produces_both(self):
        ct, body = _multipart(
            {"note": "hello"},
            {"doc": ("f.txt", "content", "text/plain")},
        )
        kwargs = self._req(ct, body).kwargs()
        self.assertIn("data", kwargs)
        self.assertIn("files", kwargs)

    def test_interleaved_produces_unified_files(self):
        # Manually build an interleaved multipart: file then field
        raw_body = (
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="f.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\nFILEDATA\r\n"
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="field"\r\n'
            f"\r\nFIELDVALUE\r\n"
            f"--{_BOUNDARY}--\r\n"
        ).encode()
        ct = f"multipart/form-data; boundary={_BOUNDARY}"
        kwargs = self._req(ct, raw_body).kwargs()
        self.assertIn("files", kwargs)
        self.assertNotIn("data", kwargs)
        files = eval(kwargs["files"])
        self.assertIn("file", files)
        self.assertIn("field", files)

    def test_empty_multipart_returns_empty_dict(self):
        ct = f"multipart/form-data; boundary={_BOUNDARY}"
        body = f"--{_BOUNDARY}--\r\n".encode()
        kwargs = self._req(ct, body).kwargs()
        self.assertEqual(kwargs, {})

    def test_file_value_has_filename_content_and_content_type(self):
        ct, body = _multipart({}, {"f": ("report.pdf", "PDFDATA", "application/pdf")})
        kwargs = self._req(ct, body).kwargs()
        files = eval(kwargs["files"])
        filename, content, ctype = files["f"]
        self.assertEqual(filename, "report.pdf")
        self.assertEqual(content, "PDFDATA")
        self.assertEqual(ctype, "application/pdf")

    def test_field_value_in_split_form_is_bare_string(self):
        ct, body = _multipart({"x": "hello"})
        kwargs = self._req(ct, body).kwargs()
        data = eval(kwargs["data"])
        self.assertEqual(data["x"], "hello")


# ---------------------------------------------------------------------------
# CodeGenerator.generate()
# ---------------------------------------------------------------------------


class TestCodeGeneratorGenerate(unittest.TestCase):
    def _gen(self, raw: bytes) -> str:
        return CodeGenerator().generate(HttpRequest.parse(raw))

    def test_method_lowercased_in_call(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn("self.session.get(", code)

    def test_post_method(self):
        code = self._gen(b"POST /api HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn("self.session.post(", code)

    def test_url_in_first_arg(self):
        code = self._gen(b"GET /path HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertIn("'http://example.com/path'", code)

    def test_no_extra_kwargs_for_bare_get(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertNotIn("params=", code)
        self.assertNotIn("headers=", code)
        self.assertNotIn("cookies=", code)

    def test_query_params_emitted(self):
        code = self._gen(
            b"GET /search?q=hello&page=2 HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        self.assertIn("params=", code)
        self.assertIn("'q': 'hello'", code)
        self.assertIn("'page': '2'", code)

    def test_query_string_stripped_from_url(self):
        code = self._gen(b"GET /search?q=x HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertNotIn("?q=x", code)
        self.assertIn("'http://example.com/search'", code)

    def test_kept_headers_emitted(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\nX-Custom: MyValue\r\n\r\n")
        self.assertIn("headers=", code)
        self.assertIn("'X-Custom': 'MyValue'", code)

    def test_host_header_not_in_headers(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: example.com\r\nX-A: B\r\n\r\n")
        self.assertNotIn("'Host':", code)
        self.assertNotIn("'host':", code)

    def test_cookies_emitted_separately(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\nCookie: s=abc\r\n\r\n")
        self.assertIn("cookies=", code)
        self.assertIn("'s': 'abc'", code)

    def test_cookie_header_not_in_headers_kwarg(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\nCookie: s=abc\r\n\r\n")
        self.assertNotIn("'Cookie':", code)

    def test_json_body_emitted(self):
        raw = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: x\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"key": "value"}'
        )
        code = self._gen(raw)
        self.assertIn("json=", code)
        self.assertIn("'key': 'value'", code)

    def test_urlencoded_body_emitted(self):
        raw = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: x\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n"
            b"\r\n"
            b"user=bob&pass=secret"
        )
        code = self._gen(raw)
        self.assertIn("data=", code)
        self.assertIn("'user': 'bob'", code)

    def test_method_signature_present(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn("async def send(self, payload: str) -> bytes:", code)

    def test_response_await_line(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn("    response = await self.session.get(", code)

    def test_return_statement(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn("    return response.content", code)

    def test_url_indented_8_spaces(self):
        code = self._gen(b"GET /path HTTP/1.1\r\nHost: x\r\n\r\n")
        url_line = next(l for l in code.splitlines() if "'http://x/path'" in l)
        self.assertTrue(url_line.startswith("        "), url_line)

    def test_closing_paren_indented_4_spaces(self):
        code = self._gen(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        paren_line = next(l for l in code.splitlines() if l.strip() == ")")
        self.assertEqual(paren_line, "    )")


# ---------------------------------------------------------------------------
# http_request_to_code() — integration
# ---------------------------------------------------------------------------


class TestHttpRequestToCode(unittest.TestCase):
    def test_simple_get(self):
        raw = b"GET /boo HTTP/1.1\r\nHost: saucisse\r\n\r\n"
        code = http_request_to_code(raw)
        self.assertIn("self.session.get(", code)
        self.assertIn("'http://saucisse/boo'", code)

    def test_post_with_json(self):
        raw = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"Content-Type: application/json\r\n"
            b"X-Custom-Header: KeepMyCase\r\n"
            b"\r\n"
            b'{"name": "bob", "age": 30}'
        )
        code = http_request_to_code(raw)
        self.assertIn("self.session.post(", code)
        self.assertIn("json=", code)
        self.assertIn("'name': 'bob'", code)
        self.assertIn("headers=", code)
        self.assertIn("'X-Custom-Header': 'KeepMyCase'", code)

    def test_get_with_all_features(self):
        raw = (
            b"GET /search?q=test HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"X-Auth: secret\r\n"
            b"Cookie: session=abc; user=bob\r\n"
            b"\r\n"
        )
        code = http_request_to_code(raw)
        self.assertIn("params=", code)
        self.assertIn("headers=", code)
        self.assertIn("cookies=", code)
        self.assertIn("'q': 'test'", code)
        self.assertIn("'X-Auth': 'secret'", code)
        self.assertIn("'session': 'abc'", code)
        self.assertIn("'user': 'bob'", code)

    def test_non_bytes_raises(self):
        with self.assertRaises(TypeError):
            http_request_to_code("GET / HTTP/1.1\r\n\r\n")

    def test_generated_code_is_valid_python(self):
        raw = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"x": 1}'
        )
        code = http_request_to_code(raw)
        try:
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            self.fail(f"Generated code is not valid Python: {e}\n{code}")


# ---------------------------------------------------------------------------
# do_import() — --force flag (CLI-level behaviour)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HttpRequest.from_url()
# ---------------------------------------------------------------------------


class TestHttpRequestFromUrl(unittest.TestCase):
    def test_method_is_get(self):
        r = HttpRequest.from_url("https://example.com/path")
        self.assertEqual(r.method, "GET")

    def test_target_is_full_url(self):
        url = "https://example.com/path?x=1"
        r = HttpRequest.from_url(url)
        self.assertEqual(r.target, url)

    def test_host_header_set(self):
        r = HttpRequest.from_url("https://example.com/path")
        self.assertEqual(r.header("Host"), "example.com")

    def test_host_with_port(self):
        r = HttpRequest.from_url("http://example.com:8080/path")
        self.assertEqual(r.header("Host"), "example.com:8080")

    def test_body_is_empty(self):
        r = HttpRequest.from_url("https://example.com/")
        self.assertEqual(r.body, b"")

    def test_url_method_returns_url_without_query(self):
        r = HttpRequest.from_url("https://example.com/path?q=test")
        self.assertEqual(r.url(), "https://example.com/path")

    def test_query_params_extracted(self):
        r = HttpRequest.from_url("https://example.com/?a=1&b=two")
        self.assertEqual(r.query_params(), {"a": "1", "b": "two"})

    def test_https_scheme_preserved(self):
        r = HttpRequest.from_url("https://secure.example.com/api")
        self.assertIn("https://", r.url())

    def test_no_extra_headers(self):
        r = HttpRequest.from_url("https://example.com/")
        # Only the Host header, which is filtered out by kept_headers()
        self.assertEqual(r.kept_headers(), {})


# ---------------------------------------------------------------------------
# _parse_input() — URL vs HTTP request detection
# ---------------------------------------------------------------------------


class TestParseInput(unittest.TestCase):
    def test_http_url_detected(self):
        r = _parse_input(b"http://example.com/path")
        self.assertEqual(r.method, "GET")
        self.assertIn("example.com", r.url())

    def test_https_url_detected(self):
        r = _parse_input(b"https://example.com/path")
        self.assertEqual(r.method, "GET")
        self.assertIn("https://", r.url())

    def test_url_with_trailing_newline(self):
        r = _parse_input(b"https://example.com/path\n")
        self.assertEqual(r.method, "GET")

    def test_url_with_trailing_crlf(self):
        r = _parse_input(b"https://example.com/path\r\n")
        self.assertEqual(r.method, "GET")

    def test_http_request_still_parsed(self):
        raw = b"POST /api HTTP/1.1\r\nHost: example.com\r\n\r\nbody"
        r = _parse_input(raw)
        self.assertEqual(r.method, "POST")
        self.assertEqual(r.body, b"body")

    def test_url_query_params_parsed(self):
        r = _parse_input(b"https://example.com/page?id=4&x=y")
        self.assertEqual(r.query_params(), {"id": "4", "x": "y"})

    def test_url_produces_no_kept_headers(self):
        r = _parse_input(b"https://example.com/path")
        self.assertEqual(r.kept_headers(), {})

    def test_invalid_request_raises(self):
        with self.assertRaises((ValueError, TypeError)):
            _parse_input(b"not a url or request")


# ---------------------------------------------------------------------------
# http_request_to_code() with URL input — integration
# ---------------------------------------------------------------------------


class TestHttpRequestToCodeFromUrl(unittest.TestCase):
    def test_url_generates_get_call(self):
        code = http_request_to_code(
            b"https://www.zixem.altervista.org/SQLi/level2.php?showprofile=4"
        )
        self.assertIn("self.session.get(", code)

    def test_url_stripped_of_query(self):
        code = http_request_to_code(
            b"https://www.zixem.altervista.org/SQLi/level2.php?showprofile=4"
        )
        self.assertIn("'https://www.zixem.altervista.org/SQLi/level2.php'", code)

    def test_url_query_params_emitted(self):
        code = http_request_to_code(
            b"https://www.zixem.altervista.org/SQLi/level2.php?showprofile=4"
        )
        self.assertIn("params=", code)
        self.assertIn("'showprofile': '4'", code)

    def test_url_no_headers_kwarg(self):
        code = http_request_to_code(b"https://example.com/path")
        self.assertNotIn("headers=", code)

    def test_url_no_cookies_kwarg(self):
        code = http_request_to_code(b"https://example.com/path")
        self.assertNotIn("cookies=", code)

    def test_url_code_is_valid_python(self):
        code = http_request_to_code(
            b"https://www.zixem.altervista.org/SQLi/level2.php?showprofile=4"
        )
        try:
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            self.fail(f"Generated code is not valid Python: {e}\n{code}")


class TestDoImportForce(unittest.IsolatedAsyncioTestCase):
    """Tests that exercise the --force flag of do_import.

    These tests use a real DesignDirectory so they touch the filesystem; each
    test uses a unique name and cleans up after itself.
    """

    _DESIGN_NAME = "__test_import_force__"

    def setUp(self):
        from ending.cli.design import DesignDirectory

        self.design_dir = DesignDirectory(self._DESIGN_NAME)
        if self.design_dir.exists():
            self.design_dir.remove()

    def tearDown(self):
        if self.design_dir.exists():
            self.design_dir.remove()

    def _ns(self, force: bool = False):
        from argparse import Namespace

        return Namespace(force=force)

    async def _import(self, raw: bytes, force: bool = False):
        import io
        from unittest.mock import AsyncMock, patch
        from ending.cli.import_ import do_import

        with patch("ending.cli.import_.sys.stdin") as mock_stdin, patch(
            "ending.cli.parse.do_edit", new_callable=AsyncMock
        ):
            mock_stdin.buffer = io.BytesIO(raw)
            await do_import(self.design_dir, self._ns(force=force))

    def _read_design(self) -> str:
        return self.design_dir.get_module_path().read_text()

    async def test_import_creates_design(self):
        await self._import(b"GET /first HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertTrue(self.design_dir.exists())
        self.assertIn("first", self._read_design())

    async def test_existing_design_without_force_does_not_overwrite(self):
        await self._import(b"GET /first HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await self._import(
            b"GET /second HTTP/1.1\r\nHost: example.com\r\n\r\n", force=False
        )
        self.assertIn("first", self._read_design())
        self.assertNotIn("second", self._read_design())

    async def test_existing_design_with_force_overwrites(self):
        await self._import(b"GET /first HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await self._import(
            b"GET /second HTTP/1.1\r\nHost: example.com\r\n\r\n", force=True
        )
        self.assertNotIn("first", self._read_design())
        self.assertIn("second", self._read_design())
