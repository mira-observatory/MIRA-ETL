from __future__ import annotations

import unicodedata


def normalise_name(value: str | None) -> str | None:
    """Preserves the published name while making whitespace deterministic.

    Case, accents, punctuation, and legal suffixes are meaningful display
    information and are deliberately retained. Search-time SQL expressions
    handle case/accent-insensitive lookup without storing a second name.
    """
    if not value:
        return None

    text = unicodedata.normalize("NFC", value)
    text = " ".join(text.split())
    return text or None
