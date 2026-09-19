"""Represent source NUL characters explicitly in PostgreSQL text/JSONB values."""
from __future__ import annotations

import json
from typing import Any


def postgres_safe(value: Any) -> Any:
    """Replace actual U+0000, never the six literal characters ``\\u0000``.

    Return a copy so source hashes and the original validation evidence remain
    unchanged. U+FFFD marks the unsupported character instead of silently
    deleting it and joining two pieces of text.
    """
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe_key = postgres_safe(key)
            if safe_key in result:
                raise ValueError("NUL replacement would merge distinct JSON keys")
            result[safe_key] = postgres_safe(item)
        return result
    if isinstance(value, list):
        return [postgres_safe(item) for item in value]
    if isinstance(value, tuple):
        return tuple(postgres_safe(item) for item in value)
    return value


def nul_paths(value: Any, path: str = "$") -> list[str]:
    """Identify affected source fields for audit, with escaped key names."""
    if isinstance(value, str):
        return [path] if "\x00" in value else []
    paths = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}[{json.dumps(str(key), ensure_ascii=False)}]"
            if isinstance(key, str) and "\x00" in key:
                paths.append(child_path + " (key)")
            paths.extend(nul_paths(item, child_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            paths.extend(nul_paths(item, f"{path}[{index}]"))
    return paths
