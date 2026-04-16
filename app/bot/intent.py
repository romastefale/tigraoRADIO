def detect_intent(text: str) -> str | None:
    if not text:
        return None

    text = text.strip().lower()

    if text in ["tocando", "tigraofm", "radinho", "qap"]:
        return "play"

    return None