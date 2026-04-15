def detect_intent(text: str) -> str | None:
    normalized = text.lower().strip()

    if any(phrase in normalized for phrase in ["tocando", "ouvindo", "agora", "musica", "música", "som", "play"]):
        return "play"

    return None
