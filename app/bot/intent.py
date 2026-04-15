def detect_intent(text: str) -> str | None:
    text = text.lower().strip()

    if any(p in text for p in ["tocando", "ouvindo", "agora", "musica", "som"]):
        return "play"

    if any(p in text for p in ["album", "disco"]):
        return "album"

    if any(p in text for p in ["artista", "banda"]):
        return "artist"

    if any(p in text for p in ["top", "favoritas", "ranking"]):
        return "ranking"

    if any(p in text for p in ["grupo", "galera"]):
        return "geeknow"

    return None
