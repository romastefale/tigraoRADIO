def detect_intent(text: str) -> str | None:
    if not text:
        return None

    text = text.strip().lower()

    if text in ["tocando", "ouvindo", "qual música", "qual musica"]:
        return "play"

    return None