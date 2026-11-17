"""Polite HTTP client (ADR-0003): rate limit, retries, redirects, limits, decoding.

Responsibilities stop at "what came back": classifying the response is
``classify``'s job. Every HTTP request — including robots.txt, sitemap.xml,
redirect hops and retries — goes through the same per-domain rate gate.
"""

from __future__ import annotations

import random
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from typing import Iterable, Protocol

import httpx

from .classify import looks_blocked_text
from .config import RunConfig
from .decode import decode_body
from .models import FetchResult, Purpose

CHUNK_SIZE = 64 * 1024
_UNSUPPORTED_ENCODINGS = ("br", "deflate", "zstd", "compress")
_RETRYABLE_ERRORS = ("timeout", "connect_error", "tls_error", "read_error")
_RETRYABLE_STATUSES = (500, 502, 504)


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def now(self): ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self):
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)


@dataclass
class _RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes | None
    compressed_bytes: int
    gzip: bool
    error: str | None
    encoding: str | None
    text: str | None
    warnings: tuple[str, ...]


class HttpClient:
    def __init__(
        self,
        config: RunConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Clock | None = None,
        domain_keys: Iterable[str] = (),
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self._domain_keys = sorted({key.lower() for key in domain_keys}, key=len, reverse=True)
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                connect=config.connect_timeout,
                read=config.timeout,
                write=config.timeout,
                pool=config.timeout,
            ),
            headers={
                "User-Agent": config.user_agent,
                "Accept-Encoding": "gzip",
                "Accept": "application/xml, application/json, text/csv, text/html;q=0.8, */*;q=0.5",
            },
        )
        self._last_request_at: dict[str, float] = {}
        self._domain_intervals: dict[str, float] = {}

    def set_domain_interval(self, domain: str, interval: float) -> None:
        """Raise the per-domain interval (used for robots.txt Crawl-delay)."""

        key = domain.lower()
        current = self._domain_intervals.get(key, self.config.per_domain_interval)
        self._domain_intervals[key] = max(current, interval)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def get(self, url: str, *, purpose: Purpose | str = Purpose.CANDIDATE) -> FetchResult:
        started = self.clock.monotonic()
        requested_url = url
        current_url = url
        visited = {url}
        redirects = 0
        warnings: list[str] = []
        requests_made = 0
        retries_used = 0
        last = _RawResponse(
            status=0,
            headers={},
            body=None,
            compressed_bytes=0,
            gzip=False,
            error=None,
            encoding=None,
            text=None,
            warnings=(),
        )

        while True:
            self._wait_for_domain(current_url)
            requests_made += 1
            try:
                response = self._request_once(current_url)
            except httpx.TimeoutException:
                response = _error_response("timeout")
            except httpx.ConnectError as exc:
                response = _error_response(_connect_error_code(exc))
            except (httpx.ReadError, httpx.RemoteProtocolError):
                response = _error_response("read_error")
            except httpx.HTTPError:
                response = _error_response("read_error")

            last = response
            warnings.extend(
                warning for warning in response.warnings if warning not in warnings
            )

            redirect_target = self._redirect_target(response)
            if redirect_target is not None:
                if redirect_target in visited:
                    return self._finish(
                        last,
                        requested_url,
                        current_url,
                        redirects,
                        warnings + ["redirect_loop"],
                        "redirect_loop",
                        requests_made,
                        started,
                    )
                if redirects >= self.config.max_redirects:
                    return self._finish(
                        last,
                        requested_url,
                        current_url,
                        redirects,
                        warnings + ["too_many_redirects"],
                        "too_many_redirects",
                        requests_made,
                        started,
                    )
                if _host(redirect_target) != _host(current_url):
                    if "cross_domain_redirect" not in warnings:
                        warnings.append("cross_domain_redirect")
                visited.add(redirect_target)
                current_url = redirect_target
                redirects += 1
                continue

            if self._should_retry(response) and retries_used < self.config.retries:
                if requests_made < self.config.max_requests_per_candidate:
                    self.clock.sleep(1.0 * (2**retries_used) + random.uniform(0, 0.5))
                    retries_used += 1
                    continue
            break

        return self._finish(
            last,
            requested_url,
            current_url,
            redirects,
            warnings,
            last.error,
            requests_made,
            started,
        )

    # -- internals ----------------------------------------------------------

    def _wait_for_domain(self, url: str) -> None:
        key = self._domain_key(url)
        now = self.clock.monotonic()
        last_at = self._last_request_at.get(key)
        interval = self._domain_intervals.get(key, self.config.per_domain_interval)
        if last_at is not None:
            wait = interval - (now - last_at)
            if wait > 0:
                self.clock.sleep(wait)
                now = self.clock.monotonic()
        self._last_request_at[key] = now

    def _domain_key(self, url: str) -> str:
        host = _host(url)
        for domain in self._domain_keys:
            if host == domain or host.endswith("." + domain):
                return domain
        return host

    def _redirect_target(self, response: _RawResponse) -> str | None:
        if response.error is not None:
            return None
        if response.status not in (301, 302, 303, 307, 308):
            return None
        location = response.headers.get("location")
        if not location:
            return None
        target = httpx.URL(response.headers.get("__final_url__", "")).join(location)
        if target.scheme not in ("http", "https"):
            # Encode the failure in a sentinel the caller recognises.
            response.error = "bad_redirect"
            return None
        return str(target)

    def _should_retry(self, response: _RawResponse) -> bool:
        if response.error in _RETRYABLE_ERRORS:
            return True
        if response.status in _RETRYABLE_STATUSES:
            return True
        if response.status == 503 and not looks_blocked_text(response.text or "", 503):
            return True
        return False

    def _request_once(self, url: str) -> _RawResponse:
        with self._client.stream("GET", url) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            headers["__final_url__"] = str(response.url)
            status = response.status_code

            encoding_header = (headers.get("content-encoding") or "").lower().strip()
            gzip_flag = encoding_header == "gzip"
            if encoding_header and encoding_header not in ("gzip", "identity"):
                if any(value in encoding_header for value in _UNSUPPORTED_ENCODINGS):
                    return _base_response(status, headers, error="unsupported_encoding")

            body, compressed_bytes, gzip_flag, error = self._read_body(response, gzip_flag)
            if error is not None:
                return _base_response(status, headers, error=error, gzip=gzip_flag)

            decoded = decode_body(body or b"", headers.get("content-type"))
            return _RawResponse(
                status=status,
                headers=headers,
                body=body,
                compressed_bytes=compressed_bytes,
                gzip=gzip_flag,
                error=decoded.error,
                encoding=decoded.encoding,
                text=decoded.text,
                warnings=decoded.warnings,
            )

    def _read_body(self, response: httpx.Response, gzip_flag: bool) -> tuple[bytes | None, int, bool, str | None]:
        chunks: list[bytes] = []
        compressed = 0
        decompressor: zlib._Decompress | None = None
        uncompressed_size = 0
        first_chunk = True

        for chunk in response.iter_raw():
            if not chunk:
                continue
            compressed += len(chunk)
            if compressed > self.config.max_bytes:
                return None, compressed, gzip_flag, "too_large"
            if first_chunk:
                first_chunk = False
                if not gzip_flag and chunk[:2] == b"\x1f\x8b":
                    gzip_flag = True
                if gzip_flag:
                    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            if decompressor is not None:
                try:
                    payload = decompressor.decompress(chunk, self.config.max_body_bytes + 1)
                except zlib.error:
                    return None, compressed, gzip_flag, "invalid_gzip"
            else:
                payload = chunk
            uncompressed_size += len(payload)
            if uncompressed_size > self.config.max_body_bytes:
                return None, compressed, gzip_flag, "too_large"
            chunks.append(payload)

        if decompressor is not None:
            try:
                tail = decompressor.flush()
            except zlib.error:
                return None, compressed, gzip_flag, "invalid_gzip"
            uncompressed_size += len(tail)
            if uncompressed_size > self.config.max_body_bytes:
                return None, compressed, gzip_flag, "too_large"
            chunks.append(tail)
            if not decompressor.eof:
                return None, compressed, gzip_flag, "invalid_gzip"

        return b"".join(chunks), compressed, gzip_flag, None

    def _finish(
        self,
        last: _RawResponse,
        requested_url: str,
        final_url: str,
        redirect_count: int,
        warnings: list[str],
        error: str | None,
        attempts: int,
        started: float,
    ) -> FetchResult:
        elapsed_ms = int(round((self.clock.monotonic() - started) * 1000))
        status = last.status or None
        return FetchResult(
            requested_url=requested_url,
            final_url=final_url,
            status=status,
            headers={key: value for key, value in last.headers.items() if key != "__final_url__"},
            body=last.body if error is None else None,
            text=last.text if error is None else None,
            encoding=last.encoding if error is None else None,
            gzip=last.gzip,
            size_bytes=len(last.body) if (last.body is not None and error is None) else 0,
            compressed_bytes=last.compressed_bytes,
            redirect_count=redirect_count,
            warnings=tuple(warnings),
            error=error,
            attempts=attempts,
            elapsed_ms=elapsed_ms,
        )


def _error_response(code: str) -> _RawResponse:
    return _RawResponse(
        status=0,
        headers={},
        body=None,
        compressed_bytes=0,
        gzip=False,
        error=code,
        encoding=None,
        text=None,
        warnings=(),
    )


def _base_response(status: int, headers: dict[str, str], *, error: str, gzip: bool = False) -> _RawResponse:
    return _RawResponse(
        status=status,
        headers=headers,
        body=None,
        compressed_bytes=0,
        gzip=gzip,
        error=error,
        encoding=None,
        text=None,
        warnings=(),
    )


def _connect_error_code(exc: httpx.ConnectError) -> str:
    cause = exc.__cause__
    if isinstance(cause, ssl.SSLError):
        return "tls_error"
    if isinstance(cause, socket.gaierror):
        return "dns_error"
    message = str(exc).lower()
    if "ssl" in message or "certificate" in message or "tls" in message:
        return "tls_error"
    if "name or service" in message or "nodename" in message or "getaddrinfo" in message:
        return "dns_error"
    return "connect_error"


def _host(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower()
    except Exception:  # pragma: no cover - defensive, URL building is local
        return ""
