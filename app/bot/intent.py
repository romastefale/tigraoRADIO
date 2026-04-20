def detect_intent(text: str) -> str | None:
    if not text:
        return None

    text = text.strip().lower()

    if text in ["tocando", "ag", "rafarl", "pipi", "bressing", "kur", "xxt", "ts", "cebrutius", "tigraofm", "djpi", "royalfm", "geeksfm", "radinho", "qap"]:
        return "play"

    return None