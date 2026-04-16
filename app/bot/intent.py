from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PLAY_TRIGGERS = {"tocando", "tigraofm", "radinho", "qap"}
MUSIC_INTENT_MARKERS = ("ouvindo", "escutando", "curtindo", "tocando", "agora e")
TRACK_LOOKUP_CONTEXT_BLOCKLIST = (
    "voce",
    "você",
    "falando",
    "dizendo",
    "pensando",
    "trabalhando",
    "gente",
    "pessoal",
)
TRACK_LOOKUP_QUERY_BLOCKLIST = {"musica", "música", "som", "essa", "aquela"}
TRACK_LOOKUP_CONNECTORS = {"de", "da", "do", "dos", "das"}
TRACK_LOOKUP_MAX_WORDS = 6


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
            if not query.strip() or len(query.strip()) < 3:
                return None

            if any(term in normalized_text for term in TRACK_LOOKUP_CONTEXT_BLOCKLIST):
                return None

            query_words = query.split()
            has_priority_connector = any(word in TRACK_LOOKUP_CONNECTORS for word in query_words)
            if has_priority_connector:
                return IntentResult(kind="track_lookup", query=query)

            has_explicit_track_intent = any(
                marker in normalized_text
                for marker in ("ouvindo", "escutando", "curtindo", "tocando", "agora")
            )
            if len(query_words) == 1 and not has_explicit_track_intent:
                return None

            if query in TRACK_LOOKUP_QUERY_BLOCKLIST:
                return None
            if len(query_words) > TRACK_LOOKUP_MAX_WORDS:
                return None

            return IntentResult(kind="track_lookup", query=query)

    return None
