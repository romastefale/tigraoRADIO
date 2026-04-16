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


def _has_strong_connector_pattern(normalized_text: str) -> bool:
    words = re.findall(r"[a-z0-9]+", normalized_text)
    if len(words) < 3:
        return False

    context_blocklist = {_normalize(term) for term in TRACK_LOOKUP_CONTEXT_BLOCKLIST}
    for idx in range(1, len(words) - 1):
        if words[idx] not in TRACK_LOOKUP_CONNECTORS:
            continue

        left_words = words[:idx]
        right_words = words[idx + 1 :]
        if not left_words or not right_words:
            continue

        if all(word in context_blocklist for word in left_words):
            continue
        if all(word in context_blocklist for word in right_words):
            continue

        return True

    return False


def _contains_marker(normalized_text: str, marker: str) -> bool:
    pattern = rf"\b{re.escape(marker).replace(r'\\ ', r'\\s+')}\b"
    return bool(re.search(pattern, normalized_text))


def detect_intent(text: str) -> IntentResult | None:
    if not text:
        return None

    normalized_text = _normalize(text.strip())

    if normalized_text in PLAY_TRIGGERS:
        return IntentResult(kind="play")

    if _has_strong_connector_pattern(normalized_text):
        query = _extract_track_query(normalized_text)
        if query:
            query_words = query.split()
            if len(query.strip()) >= 3 and len(query_words) <= TRACK_LOOKUP_MAX_WORDS:
                if query not in TRACK_LOOKUP_QUERY_BLOCKLIST:
                    return IntentResult(kind="track_lookup", query=query)

    has_musical_intent = any(
        _contains_marker(normalized_text, marker) for marker in MUSIC_INTENT_MARKERS
    )
    if has_musical_intent:
        query = _extract_track_query(normalized_text)
        if not query or len(query.strip()) < 3:
            return None

        query_words = query.split()
        if not query_words:
            return None

        normalized_context_blocklist = {_normalize(term) for term in TRACK_LOOKUP_CONTEXT_BLOCKLIST}
        if any(word in normalized_context_blocklist for word in query_words):
            return None

        if query in TRACK_LOOKUP_QUERY_BLOCKLIST:
            return None
        if len(query_words) > TRACK_LOOKUP_MAX_WORDS:
            return None

        has_explicit_track_intent = any(
            _contains_marker(normalized_text, marker) for marker in MUSIC_INTENT_MARKERS
        )
        if len(query_words) == 1 and not has_explicit_track_intent:
            return None

        return IntentResult(kind="track_lookup", query=query)

    return None
