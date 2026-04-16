from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PLAY_TRIGGERS = {"tocando", "tigraofm", "radinho", "qap"}
MUSIC_INTENT_MARKERS = ("ouvindo", "escutando", "curtindo", "tocando", "agora e")


@dataclass(frozen=True)
class IntentResult:
    kind: str
    query: str | None = None


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized.lower()


def _extract_track_query(normalized_text: str) -> str | None:
    query = normalized_text
    for marker in MUSIC_INTENT_MARKERS:
        query = query.replace(marker, " ")

    query = re.sub(r"\bto\b", " ", query)
    query = re.sub(r"[^a-z0-9\s]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query or None


def detect_intent(text: str) -> IntentResult | None:
    if not text:
        return None

    normalized_text = _normalize(text.strip())

    if normalized_text in PLAY_TRIGGERS:
        return IntentResult(kind="play")

    if any(marker in normalized_text for marker in MUSIC_INTENT_MARKERS):
        query = _extract_track_query(normalized_text)
        if query:
            return IntentResult(kind="track_lookup", query=query)

    return None
