"""Strip secrets before a trace line or content blob is written.

Built-in patterns aim at credentials, not ordinary prose. A value is removed
when it matches a known token shape, sits in an Authorization header, is the
value of a secret key (``password``, ``api_key``, ``access_token``, …), or is
assigned to an environment name that marks it secret (``GITHUB_TOKEN``,
``OPENAI_API_KEY``, ``MAX_TOKENS`` is not one of those).

The stored text keeps the surrounding words. The value becomes
``[REDACTED]``. Digests are recomputed from that text so ``read_content``
matches the bytes on disk.

Extra patterns are regular expressions. Each match is replaced in full.
``AGENTS_TRACE_REDACT=0`` turns the default redactor off.
``AGENTS_TRACE_REDACT_EXTRA`` is a JSON array of extra patterns.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional, Sequence

from .content import content_sha256
from .models import TraceEvent

REDACTED = "[REDACTED]"

_ENV_DISABLE = frozenset({"0", "false", "no", "off", "disabled"})
_HARD_PARTS = frozenset(
    {
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "PASSPHRASE",
        "PWD",
        "APIKEY",
        "CREDENTIAL",
        "CREDENTIALS",
    }
)
_COUNTER_PARTS = frozenset(
    {
        "COUNT",
        "TOTAL",
        "LIMIT",
        "MAX",
        "MIN",
        "NUM",
        "NUMBER",
        "SIZE",
        "LENGTH",
        "USAGE",
        "BUDGET",
        "PERCENT",
        "PCT",
    }
)
# Whole value, including short ones. These names are the secret.
_ANY_SUFFIXES = (
    "password",
    "passwd",
    "passphrase",
    "pwd",
    "client_secret",
    "private_key",
    "session_secret",
    "aws_secret_access_key",
)
# Explicit literals (quoted or dict values) always. Bare ``key=shortword`` stays.
_TOKEN_SUFFIXES = (
    "api_key",
    "api_token",
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "aws_access_key_id",
    "secret",
    "token",
)
# Only when the value looks like a credential. ``authorization: enabled`` stays.
_SHAPED_SUFFIXES = ("proxy_authorization", "authorization")

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_USERINFO = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]*:)([^\s/@]+)(@)"
)
_AUTH = re.compile(
    r"(?i)(\b(?:proxy-)?authorization\b\s*[:=]\s*[\"']?\s*)"
    r"(bearer|basic|token)(\s+)([A-Za-z0-9._~+/=-]{8,})"
)
_BEARER = re.compile(
    r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{24,})(?![A-Za-z0-9._~+/=-])"
)
_JSON_PAIR = re.compile(
    r"(?P<q1>[\"'])(?P<key>[A-Za-z][A-Za-z0-9_\-]{0,80})(?P=q1)"
    r"(?P<sep>\s*:\s*)(?P<q>[\"'])(?P<qval>(?:\\.|(?!(?P=q)).)*)(?P=q)"
)
_ASSIGN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<key>[A-Za-z][A-Za-z0-9_\-]{0,80})(?P<sep>\s*=\s*)"
    r"(?:(?P<q>[\"'])(?P<qval>(?:\\.|(?!(?P=q)).)*)(?P=q)|(?P<val>[^\s\"',;&]+))"
)
_COLON = re.compile(
    r"(?<![A-Za-z0-9_])(?P<key>[A-Za-z][A-Za-z0-9_\-]{0,80})(?P<sep>\s*:\s*)"
    r"(?:(?P<q>[\"'])(?P<qval>(?:\\.|(?!(?P=q)).)*)(?P=q)|(?P<val>[^\s\"',;&]+))"
)

# (pattern, require a digit somewhere in the match).
# Digit gate keeps ``sk-implementation-details`` and other prose intact.
# Fixed vendor prefixes that are already unambiguous skip the gate.
_TOKEN_SPECS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (
        re.compile(
            r"(?<![A-Za-z0-9])sk-(?:proj-|ant-api\d+-|ant-|live-)?"
            r"[A-Za-z0-9][A-Za-z0-9_\-]{19,}(?![A-Za-z0-9_\-])"
        ),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}(?![A-Za-z0-9_])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])xapp-\d-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])"),
        False,
    ),
    (
        re.compile(
            r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"
        ),
        False,
    ),
    (
        re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"),
        False,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{35}(?![A-Za-z0-9_\-])"),
        True,
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9])[rs]k_(?:live|test)_[0-9A-Za-z]{16,}(?![A-Za-z0-9])"
        ),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])pypi-[A-Za-z0-9_\-]{16,}(?![A-Za-z0-9_\-])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])dop_v1_[a-f0-9]{32,}(?![a-f0-9])"),
        False,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])shpat_[a-fA-F0-9]{32}(?![a-fA-F0-9])"),
        True,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])ya29\.[0-9A-Za-z_\-]{20,}(?![A-Za-z0-9_\-])"),
        True,
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?![A-Za-z0-9_])"
        ),
        True,
    ),
)

_PREFIXES = (
    "sk-",
    "sk_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xoxa-",
    "xoxb-",
    "xoxp-",
    "xoxr-",
    "xoxs-",
    "xapp-",
    "AKIA",
    "ASIA",
    "AIza",
    "hf_",
    "npm_",
    "eyJ",
    "SG.",
    "pypi-",
    "gsk_",
    "dop_v1_",
    "shpat_",
    "ya29.",
    "hooks.slack.com",
    "-----BEGIN",
)


def _env_enabled() -> bool:
    raw = os.environ.get("AGENTS_TRACE_REDACT", "").strip().lower()
    return raw not in _ENV_DISABLE


def _parse_env_patterns(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "AGENTS_TRACE_REDACT_EXTRA must be a JSON array of strings"
        ) from exc
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("AGENTS_TRACE_REDACT_EXTRA must be a JSON array of strings")
    return data


def _compile(pattern: str) -> re.Pattern[str]:
    if not isinstance(pattern, str) or pattern == "":
        raise ValueError("redaction pattern must be a non-empty string")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid redaction pattern: {pattern}") from exc


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().strip("'\"").lower()).strip("_")


def _has_suffix(norm: str, suffixes: tuple[str, ...]) -> bool:
    return any(norm == suffix or norm.endswith("_" + suffix) for suffix in suffixes)


def _env_parts(name: str) -> list[str]:
    return [part for part in re.sub(r"[^A-Za-z0-9]+", "_", name).upper().split("_") if part]


def _env_marked_secret(name: str) -> bool:
    """True when an environment-style name says the value is a secret.

    ``GITHUB_TOKEN`` and ``OPENAI_API_KEY`` match. ``TOKEN_COUNT`` and
    ``MAX_TOKENS`` do not. A bare ``TOKEN`` does not: that word is too common.
    """
    parts = _env_parts(name)
    if not parts or parts == ["TOKEN"]:
        return False
    hard = any(part in _HARD_PARTS for part in parts)
    if any(part in _COUNTER_PARTS for part in parts) and not hard:
        return False
    if hard or "TOKEN" in parts:
        return True
    if "API" in parts and "KEY" in parts:
        return True
    if "ACCESS" in parts and "KEY" in parts:
        return True
    if "PRIVATE" in parts and "KEY" in parts:
        return True
    return False


def _looks_env_name(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", key.strip().strip("'\"")))


def _key_class(key: str) -> str:
    """``any`` wipes every value. ``token`` wipes explicit literals. ``shaped`` wipes credential-like values."""
    raw = key.strip().strip("'\"")
    norm = _norm_key(raw)
    if not norm:
        return ""
    if _has_suffix(norm, _ANY_SUFFIXES):
        return "any"
    if _looks_env_name(raw) and _env_marked_secret(raw):
        return "any"
    if _has_suffix(norm, _TOKEN_SUFFIXES):
        return "token"
    if _has_suffix(norm, _SHAPED_SUFFIXES):
        return "shaped"
    if _env_marked_secret(raw):
        return "token"
    return ""


def _looks_credential(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    for scheme in ("bearer ", "basic "):
        if lower.startswith(scheme):
            rest = value[len(scheme) :].strip()
            if len(rest) >= 8 and not any(ch.isspace() for ch in rest):
                return True
    if any(ch.isspace() for ch in value):
        return False
    if len(value) >= 12:
        return True
    if len(value) >= 6 and any(not ch.isalpha() for ch in value):
        return True
    return False


def _should_wipe(kind: str, value: str, *, explicit: bool, replacement: str) -> bool:
    if not kind or value == "" or value == replacement:
        return False
    if kind == "any":
        return True
    if explicit and kind == "token":
        return True
    if kind in {"token", "shaped"} and _looks_credential(value):
        return True
    return False


def _has_trigger(text: str) -> bool:
    if "=" in text or ":" in text or "@" in text:
        return True
    if any(prefix in text for prefix in _PREFIXES):
        return True
    lower = text.lower()
    return "bearer " in lower or "private key" in lower


def _sync_hash(meta: dict[str, Any], prefix: str, before: Any, after: Any) -> None:
    if not isinstance(before, str) or not isinstance(after, str) or before == after:
        return
    flag = f"{prefix}truncated" if prefix else "truncated"
    if meta.get(flag):
        return
    sha = content_sha256(after)
    sha_key = f"{prefix}sha256" if prefix else "sha256"
    digest_key = f"{prefix}digest" if prefix else "digest"
    chars_key = f"{prefix}chars" if prefix else "chars"
    if sha_key in meta:
        meta[sha_key] = sha
    if digest_key in meta:
        meta[digest_key] = sha[:12]
    if chars_key in meta:
        meta[chars_key] = len(after)


class Redactor:
    """Built-in patterns plus optional extras. Safe to share across stores."""

    def __init__(
        self,
        extra_patterns: Optional[Sequence[str]] = None,
        *,
        enabled: Optional[bool] = None,
        use_env: bool = True,
        replacement: str = REDACTED,
    ) -> None:
        if not replacement:
            raise ValueError("replacement must be non-empty")
        self.replacement = replacement
        self._enabled = enabled
        self.use_env = use_env
        self._env_raw: Optional[str] = None
        self._env_compiled: tuple[re.Pattern[str], ...] = ()
        self._extra = tuple(_compile(pattern) for pattern in (extra_patterns or ()))

    def is_enabled(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled)
        if self.use_env:
            return _env_enabled()
        return True

    def set_extra_patterns(self, patterns: Sequence[str]) -> None:
        compiled = tuple(_compile(pattern) for pattern in patterns)
        self._extra = compiled

    def _compiled_extras(self) -> tuple[re.Pattern[str], ...]:
        if not self.use_env:
            return self._extra
        raw = os.environ.get("AGENTS_TRACE_REDACT_EXTRA", "")
        if raw == self._env_raw:
            return self._env_compiled + self._extra
        compiled = tuple(_compile(pattern) for pattern in _parse_env_patterns(raw))
        self._env_raw = raw
        self._env_compiled = compiled
        return compiled + self._extra

    def _sub_token(self, pattern: re.Pattern[str], require_digit: bool, text: str) -> str:
        replacement = self.replacement

        def repl(match: re.Match[str]) -> str:
            token = match.group(0)
            if require_digit and not any(ch.isdigit() for ch in token):
                return token
            return replacement

        return pattern.sub(repl, text)

    def _sub_url(self, match: re.Match[str]) -> str:
        if match.group(2) == self.replacement:
            return match.group(0)
        return f"{match.group(1)}{self.replacement}{match.group(3)}"

    def _sub_auth(self, match: re.Match[str]) -> str:
        if match.group(4) == self.replacement:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{match.group(3)}{self.replacement}"

    def _sub_bearer(self, match: re.Match[str]) -> str:
        if match.group(2) == self.replacement:
            return match.group(0)
        return f"{match.group(1)}{self.replacement}"

    def _sub_pair(self, match: re.Match[str]) -> str:
        key = match.group("key")
        kind = _key_class(key)
        if not kind:
            return match.group(0)
        quote = match.group("q")
        value = match.group("qval") if quote else match.group("val")
        if value is None or not _should_wipe(
            kind, value, explicit=quote is not None, replacement=self.replacement
        ):
            return match.group(0)
        key_quote = match.groupdict().get("q1")
        shown = f"{key_quote}{key}{key_quote}" if key_quote else key
        if quote:
            return f"{shown}{match.group('sep')}{quote}{self.replacement}{quote}"
        return f"{shown}{match.group('sep')}{self.replacement}"

    def _apply_pairs(self, text: str, pattern: re.Pattern[str]) -> str:
        """Replace secret assignments.

        A non-secret key does not consume the rest of the match, so
        ``note: password: hunter2`` still redacts ``hunter2``.
        """
        out: list[str] = []
        pos = 0
        while pos < len(text):
            match = pattern.search(text, pos)
            if match is None:
                out.append(text[pos:])
                break
            rendered = self._sub_pair(match)
            if rendered == match.group(0):
                nxt = match.end("key")
                if nxt <= pos:
                    nxt = pos + 1
                out.append(text[pos:nxt])
                pos = nxt
                continue
            out.append(text[pos : match.start()])
            out.append(rendered)
            pos = match.end()
        return "".join(out)

    def redact_text(self, text: str) -> str:
        if not self.is_enabled() or not isinstance(text, str) or text == "":
            return text
        if text == self.replacement:
            return text
        env_raw = os.environ.get("AGENTS_TRACE_REDACT_EXTRA", "") if self.use_env else ""
        has_extra = bool(self._extra) or bool(env_raw.strip())
        triggered = _has_trigger(text)
        if not has_extra and not triggered:
            return text
        extras = self._compiled_extras() if has_extra else ()
        out = text
        if triggered:
            out = _PEM.sub(self.replacement, out)
            out = _URL_USERINFO.sub(self._sub_url, out)
            out = _AUTH.sub(self._sub_auth, out)
            out = _BEARER.sub(self._sub_bearer, out)
            for pattern, require_digit in _TOKEN_SPECS:
                out = self._sub_token(pattern, require_digit, out)
            out = self._apply_pairs(out, _JSON_PAIR)
            out = self._apply_pairs(out, _ASSIGN)
            out = self._apply_pairs(out, _COLON)
        for pattern in extras:
            out = pattern.sub(self.replacement, out)
        return out

    def redact_obj(self, value: Any, *, key: Optional[str] = None, force: bool = False) -> Any:
        if not self.is_enabled():
            return value
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for raw_key, raw_val in value.items():
                key_text = raw_key if isinstance(raw_key, str) else None
                kind = _key_class(key_text) if key_text else ""
                child_force = force or kind in {"any", "token"}
                stored_key = self.redact_text(raw_key) if isinstance(raw_key, str) else raw_key
                out[stored_key] = self.redact_obj(raw_val, key=key_text, force=child_force)
            return out
        if isinstance(value, list):
            return [self.redact_obj(item, force=force) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_obj(item, force=force) for item in value)
        if isinstance(value, str):
            if value == "" or value == self.replacement:
                return value
            if force:
                return self.replacement
            if key and _should_wipe(
                _key_class(key), value, explicit=True, replacement=self.replacement
            ):
                return self.replacement
            return self.redact_text(value)
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            if force or (key and _key_class(key) in {"any", "token"}):
                return self.replacement
            return value
        return value

    def redact_event(self, event: TraceEvent) -> None:
        """Mutate ``event`` in place. Caller-owned dicts are copied, not edited."""
        if not self.is_enabled():
            return
        meta = event.metadata if isinstance(event.metadata, dict) else None
        before_content = meta.get("content") if meta else None
        before_prompt = meta.get("prompt") if meta else None
        before_result = event.result
        before_error = event.error
        before_stack = event.stack

        if isinstance(event.session, str):
            event.session = self.redact_text(event.session)
        if isinstance(event.tool, str):
            event.tool = self.redact_text(event.tool)
        if isinstance(event.file, str):
            event.file = self.redact_text(event.file)
        if isinstance(event.model, str):
            event.model = self.redact_text(event.model)
        event.args = self.redact_obj(event.args)
        event.result = self.redact_obj(event.result)
        event.error = self.redact_text(event.error) if isinstance(event.error, str) else event.error
        event.stack = self.redact_text(event.stack) if isinstance(event.stack, str) else event.stack
        event.metadata = self.redact_obj(event.metadata)

        meta = event.metadata if isinstance(event.metadata, dict) else None
        if meta is None:
            return
        if isinstance(before_content, str):
            _sync_hash(meta, "", before_content, meta.get("content"))
        elif isinstance(before_prompt, str) and "content" not in meta:
            _sync_hash(meta, "", before_prompt, meta.get("prompt"))
        if isinstance(before_result, str):
            _sync_hash(meta, "result_", before_result, event.result if isinstance(event.result, str) else None)
        if isinstance(before_error, str):
            _sync_hash(meta, "error_", before_error, event.error if isinstance(event.error, str) else None)
        if isinstance(before_stack, str):
            _sync_hash(meta, "stack_", before_stack, event.stack if isinstance(event.stack, str) else None)


_default: Optional[Redactor] = None


def default_redactor() -> Redactor:
    """Process-wide redactor. Created on first use so env is visible."""
    global _default
    if _default is None:
        _default = Redactor(use_env=True, enabled=None)
    return _default


def configure_redaction(
    *,
    enabled: Optional[bool] = None,
    extra_patterns: Optional[Sequence[str]] = None,
    replacement: Optional[str] = None,
    follow_env: bool = False,
) -> Redactor:
    """Update the process-wide redactor used by stores that do not pass their own.

    ``extra_patterns`` replaces configured extras. Environment extras from
    ``AGENTS_TRACE_REDACT_EXTRA`` still apply on top. ``follow_env=True``
    clears an ``enabled`` pin so ``AGENTS_TRACE_REDACT`` decides again.
    """
    redactor = default_redactor()
    if follow_env:
        redactor._enabled = None
    elif enabled is not None:
        redactor._enabled = enabled
    if extra_patterns is not None:
        redactor.set_extra_patterns(extra_patterns)
    if replacement is not None:
        if not replacement:
            raise ValueError("replacement must be non-empty")
        redactor.replacement = replacement
    return redactor
