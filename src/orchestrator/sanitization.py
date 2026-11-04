"""Utilities for keeping remote error details free of credentials."""

from __future__ import annotations

import ast
import re
from typing import Any, Iterable

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "password",
    "authorization",
    "secret",
    "cookie",
}
_KEY_NAMES = r"(?:access[_-]?token|refresh[_-]?token|token|password|authorization|secret|cookie)"
_QUOTED_VALUE_RE = re.compile(
    rf"(?i)(?P<prefix>['\"]?{_KEY_NAMES}['\"]?\s*[:=]\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)
_UNQUOTED_VALUE_RE = re.compile(
    rf"(?i)(?P<prefix>['\"]?{_KEY_NAMES}['\"]?\s*[:=]\s*)(?P<value>(?!['\"]|\[REDACTED\])[^,;&\s}}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([^\s,;&}\]]+)")


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return normalized in SENSITIVE_KEYS or any(
        part in normalized for part in ("token", "password", "authorization", "secret", "cookie")
    )


def _sanitize_text(value: str, secret_values: set[str]) -> str:
    result = value
    # Replace known values first, including values containing spaces or punctuation.
    for secret in sorted(secret_values, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    result = _BEARER_RE.sub(lambda match: match.group(1) + REDACTED, result)
    result = _QUOTED_VALUE_RE.sub(
        lambda match: match.group("prefix") + match.group("quote") + REDACTED + match.group("quote"),
        result,
    )
    return _UNQUOTED_VALUE_RE.sub(lambda match: match.group("prefix") + REDACTED, result)


def sanitize_sensitive(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact credential fields and credential-like text.

    Strings are handled both as ordinary text and as serialized JSON/Python repr.
    The latter is deliberately parsed first when possible, so nested structures and
    quoted values cannot evade the key-based redaction; the text pass preserves the
    original representation for non-literal error messages.
    """
    secret_values = {str(item) for item in secrets if item}
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else sanitize_sensitive(item, secret_values)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        sanitized = [sanitize_sensitive(item, secret_values) for item in value]
        return type(value)(sanitized) if isinstance(value, tuple) else sanitized
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list, tuple)):
            sanitized = sanitize_sensitive(parsed, secret_values)
            if isinstance(parsed, dict) or isinstance(parsed, tuple):
                # repr retains Python repr input and is safe after recursive redaction.
                return repr(sanitized)
        return _sanitize_text(value, secret_values)
    return value
