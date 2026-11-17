"""Shared fixtures: local fixture files, a virtual clock, fake transports."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class FakeClock:
    """Virtual clock: sleeping advances time instead of blocking the test."""

    monotonic_time: float = 1_000.0
    now_time: datetime = field(
        default_factory=lambda: datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)
    )
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.monotonic_time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds

    def now(self) -> datetime:
        return self.now_time


@dataclass
class RecordedRequest:
    url: str
    at: float
    headers: dict[str, str]


class _RawStream(httpx.SyncByteStream):
    """Yields stored bytes untouched: no httpx-level gzip decoding."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def __iter__(self):
        if self._content:
            yield self._content


@dataclass
class Resp:
    """Reusable response spec: every request gets a fresh, unread response."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def build(self) -> httpx.Response:
        return httpx.Response(
            self.status,
            headers=self.headers,
            stream=_RawStream(self.body),
        )


def as_spec(response: httpx.Response) -> Resp:
    body = getattr(response, "_content", None)
    if body is None:
        body = b"".join(response.stream) if hasattr(response, "stream") else b""
    return Resp(status=response.status_code, headers=dict(response.headers), body=body)


class FakeTransport(httpx.BaseTransport):
    """httpx transport returning canned responses and recording requests."""

    def __init__(self, routes, *, clock: FakeClock | None = None):
        self.routes = routes
        self.clock = clock
        self.requests: list[RecordedRequest] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(
            RecordedRequest(
                url=url,
                at=self.clock.monotonic() if self.clock else 0.0,
                headers=dict(request.headers),
            )
        )
        handler = self.routes.get(url)
        if handler is None:
            return Resp(404, {"content-type": "text/plain"}, b"not found").build()
        if callable(handler):
            produced = handler(request)
        else:
            produced = handler
        spec = produced if isinstance(produced, Resp) else as_spec(produced)
        return spec.build()

    def urls(self) -> list[str]:
        return [item.url for item in self.requests]

    def count(self, url: str) -> int:
        return sum(1 for item in self.requests if item.url == url)


def xml_response(text: str, *, status: int = 200, encoding: str = "utf-8") -> Resp:
    return Resp(status, {"content-type": f"application/xml; charset={encoding}"}, text.encode(encoding))


def html_response(text: str, *, status: int = 200, encoding: str = "utf-8") -> Resp:
    return Resp(status, {"content-type": f"text/html; charset={encoding}"}, text.encode(encoding))


def json_response(payload) -> Resp:
    return Resp(200, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))


def gzip_response(text: str) -> Resp:
    return Resp(
        200,
        {"content-type": "application/xml", "content-encoding": "gzip"},
        gzip.compress(text.encode("utf-8")),
    )


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def read_fixture():
    def _read(name: str, encoding: str = "utf-8") -> str:
        return (FIXTURES / name).read_text(encoding=encoding)

    return _read


@pytest.fixture
def read_fixture_bytes():
    def _read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _read


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
