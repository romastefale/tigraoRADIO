def detect_intent(text: str) -> str | None:
    normalized = text.lower().strip()

    play_phrases = [
        "tocando",
        "ouvindo",
        "agora",
        "música",
        "musica",
        "som",
        "play",
        "qual musica",
        "qual música",
        "qual som",
        "manda som",
        "o que ta tocando",
        "oq ta tocando",
    ]

    if any(phrase in normalized for phrase in play_phrases):
        return "play"

    return None
