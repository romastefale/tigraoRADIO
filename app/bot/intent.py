def detect_intent(text: str) -> str | None:
    if not text:
        return None

    text = text.lower().strip()

    # PLAY
    if any(p in text for p in [
        "tocando", "ouvindo", "agora", "musica", "som",
        "manda som", "qual musica", "qual som"
    ]):
        return "play"

    # ALBUM
    if any(p in text for p in [
        "album", "disco", "qual album"
    ]):
        return "album"

    # ARTIST
    if any(p in text for p in [
        "artista", "banda", "quem canta", "quem é"
    ]):
        return "artist"

    # RANKING
    if any(p in text for p in [
        "top", "favoritas", "mais ouvidas", "ranking", "hits"
    ]):
        return "ranking"

    # GROUP
    if any(p in text for p in [
        "grupo", "geral", "galera", "todo mundo"
    ]):
        return "geeknow"

    return None
