"""robots.txt handling (ADR-0003): allow/deny, crawl-delay, refusals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from .classify import blocked_reason
from .config import RunConfig
from .http_client import HttpClient
from .models import Purpose

ROBOTS_MAX_BYTES = 512 * 1024


class RobotsDecision(str, Enum):
    ALLOWED = "allowed"
    DISALLOWED = "disallowed"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


@dataclass
class _Group:
    agents: tuple[str, ...]
    rules: list[_Rule]
    crawl_delay: float | None = None
    _allow_len: int = 0
    _disallow_len: int = 0


@dataclass
class _OriginState:
    decision: RobotsDecision
    groups: tuple[_Group, ...] = ()
    detail: str | None = None


class RobotsGate:
    """Loads robots.txt once per origin and answers allow/deny questions."""

    def __init__(self, http: HttpClient, config: RunConfig) -> None:
        self._http = http
        self._config = config
        self._cache: dict[tuple[str, str, int | None], _OriginState] = {}

    def check(self, url: str) -> RobotsDecision:
        state = self._state(url)
        if state.decision is not RobotsDecision.ALLOWED:
            return state.decision
        group = self._group_for(state)
        if group is None:
            return RobotsDecision.ALLOWED
        path = _path_of(url)
        if _is_allowed(group, path):
            return RobotsDecision.ALLOWED
        return RobotsDecision.DISALLOWED

    def failure(self, url: str) -> str | None:
        """Why robots.txt was blocked/unavailable, for ``reason_detail``."""

        return self._state(url).detail

    def crawl_delay(self, url: str) -> float | None:
        state = self._state(url)
        if state.decision is not RobotsDecision.ALLOWED:
            return None
        group = self._group_for(state)
        return group.crawl_delay if group else None

    # -- internals ----------------------------------------------------------

    def _state(self, url: str) -> _OriginState:
        parts = urlsplit(url)
        origin = (parts.scheme, (parts.hostname or "").lower(), parts.port)
        if origin in self._cache:
            return self._cache[origin]

        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        result = self._http.get(robots_url, purpose=Purpose.ROBOTS)
        state = self._evaluate(result)
        self._cache[origin] = state
        return state

    def _evaluate(self, result) -> _OriginState:
        if result.error is not None:
            return _OriginState(RobotsDecision.UNAVAILABLE, detail=result.error)
        status = result.status
        if status is None:
            return _OriginState(RobotsDecision.UNAVAILABLE, detail="no response")
        if status in (401, 403, 429, 451):
            return _OriginState(RobotsDecision.BLOCKED, detail=f"HTTP {status}")
        text = result.text or ""
        marker = blocked_reason(text, status)
        if marker:
            return _OriginState(RobotsDecision.BLOCKED, detail=marker)
        if status >= 500:
            return _OriginState(RobotsDecision.UNAVAILABLE, detail=f"HTTP {status}")
        if status in (404, 410) or 400 <= status < 500:
            return _OriginState(RobotsDecision.ALLOWED)
        if status != 200:
            return _OriginState(RobotsDecision.UNAVAILABLE, detail=f"HTTP {status}")
        text = text[:ROBOTS_MAX_BYTES]
        return _OriginState(RobotsDecision.ALLOWED, tuple(_parse_groups(text)))

    def _group_for(self, state: _OriginState) -> _Group | None:
        best: _Group | None = None
        best_len = -1
        for group in state.groups:
            for agent in group.agents:
                if agent == "*":
                    if best is None:
                        best = group
                        best_len = max(best_len, 0)
                    continue
                if "feedscan".startswith(agent.lower()) or agent.lower() in ("feedscan", "feedscan/0.1.0"):
                    if len(agent) > best_len:
                        best = group
                        best_len = len(agent)
        return best


def _parse_groups(text: str) -> list[_Group]:
    groups: list[_Group] = []
    current: _Group | None = None
    in_agents = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            if current is None or not in_agents:
                current = _Group(agents=(), rules=[])
                groups.append(current)
                in_agents = True
            current.agents = current.agents + (value.lower(),)
            continue

        if current is None:
            continue
        in_agents = False

        if field == "allow":
            rule = _compile_rule(value, allow=True)
            if rule is not None:
                current.rules.append(rule)
        elif field == "disallow":
            rule = _compile_rule(value, allow=False)
            if rule is not None:
                current.rules.append(rule)
        elif field == "crawl-delay":
            try:
                current.crawl_delay = float(value)
            except ValueError:
                pass

    return groups


def _compile_rule(value: str, *, allow: bool) -> _Rule | None:
    if not value:
        return None
    pattern = value
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    elif not regex.endswith("$"):
        regex += ".*"
    return _Rule(allow=allow, pattern=pattern, regex=re.compile("^" + regex))


def _is_allowed(group: _Group, path: str) -> bool:
    best_len = -1
    allowed = True
    for rule in group.rules:
        if rule.regex.match(path):
            length = len(rule.pattern)
            if length > best_len or (length == best_len and rule.allow):
                best_len = length
                allowed = rule.allow
    return allowed


def _path_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return path
