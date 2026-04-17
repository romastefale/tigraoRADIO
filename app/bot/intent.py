def detect_intent(text: str) -> str | None:
    if not text:
        return None

    text = text.strip().lower()

    if text in ["tocando", "cebrutius", "tigraofm", "djpi", "royalfm", "geeksfm", "radinho", "qap"]:
        return "play"

    if text in ["pim", "strm", "pidj", "mv"]:
        return "playimg"

    return None
