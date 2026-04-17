from __future__ import annotations

from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont


CARD_WIDTH = 1080
CARD_HEIGHT = 1350
COVER_SIZE = 1080
BOTTOM_AREA_HEIGHT = CARD_HEIGHT - COVER_SIZE
TEXT_PADDING_X = 56
TITLE_MAX_WIDTH = CARD_WIDTH - (TEXT_PADDING_X * 2)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font with graceful fallback for environments without custom fonts."""
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    ]

    for font_name in candidates:
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue

    return ImageFont.load_default()


def _truncate_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if not text:
        return ""

    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "…"
    for i in range(len(text), 0, -1):
        candidate = f"{text[:i].rstrip()}{ellipsis}"
        if draw.textlength(candidate, font=font) <= max_width:
            return candidate

    return ellipsis


def generate_card(
    cover_url: str,
    title: str,
    artist: str,
    album: str,
    user: str,
    output_path: str,
) -> None:
    """Generate a playback card image and save it to ``output_path``.

    The resulting card has a square cover area at the top and an extended blurred
    lower section for metadata text.
    """
    response = requests.get(cover_url, timeout=15)
    response.raise_for_status()

    cover = Image.open(BytesIO(response.content)).convert("RGB")
    cover = cover.resize((COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)

    blurred = cover.filter(ImageFilter.GaussianBlur(radius=28))
    bottom_extension = blurred.crop((0, COVER_SIZE - BOTTOM_AREA_HEIGHT, CARD_WIDTH, COVER_SIZE))

    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), color=(0, 0, 0))
    card.paste(cover, (0, 0))
    card.paste(bottom_extension, (0, COVER_SIZE))

    overlay = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 96))
    card = Image.alpha_composite(card.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(card)
    pretitle_font = _load_font(42, bold=False)
    title_font = _load_font(66, bold=True)
    meta_font = _load_font(44, bold=False)

    y = COVER_SIZE + 48
    now_playing = f"🎧 {user} está ouvindo…"
    draw.text((TEXT_PADDING_X, y), now_playing, fill=(230, 230, 230, 255), font=pretitle_font)

    y += 74
    safe_title = _truncate_text(draw, title or "", title_font, TITLE_MAX_WIDTH)
    safe_artist = _truncate_text(draw, artist or "", meta_font, TITLE_MAX_WIDTH)
    safe_album = _truncate_text(draw, album or "", meta_font, TITLE_MAX_WIDTH)

    draw.text((TEXT_PADDING_X, y), safe_title, fill=(255, 255, 255, 255), font=title_font)
    y += 86

    draw.text((TEXT_PADDING_X, y), safe_artist, fill=(225, 225, 225, 255), font=meta_font)
    y += 58

    draw.text((TEXT_PADDING_X, y), safe_album, fill=(205, 205, 205, 255), font=meta_font)

    card.convert("RGB").save(output_path, format="JPEG", quality=92)
