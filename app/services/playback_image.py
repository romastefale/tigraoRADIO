from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# 🔧 corrigido (antes era app/cache/playback)
CACHE_DIR = "./cache/playback"

CANVAS_WIDTH = 1080
COVER_SIZE = 1080
BOTTOM_HEIGHT = 360
CANVAS_HEIGHT = COVER_SIZE + BOTTOM_HEIGHT


def save_atomic(image: Image.Image, final_path: str) -> None:
    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    with tempfile.NamedTemporaryFile(delete=False, dir=os.path.dirname(final_path)) as tmp:
        temp_path = tmp.name

    try:
        image.save(temp_path, format="JPEG", quality=90)
        os.replace(temp_path, final_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _safe_name(cache_key: str) -> str:
    hashed = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return f"{hashed}.jpg"


def _extract_track_id(track_id: str) -> str:
    return (track_id or "").strip()


# 🔥 corrigido: download mais robusto + logs
def _download_cover(cover_url: str) -> Image.Image | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(cover_url)
            response.raise_for_status()

        image = Image.open(io.BytesIO(response.content))
        image.load()
        return image.convert("RGB")

    except Exception as exc:
        logger.error("PLAYBACK: cover download failed", exc_info=exc)
        return None


def _load_fonts() -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont]:
    try:
        return (
            ImageFont.truetype("DejaVuSans.ttf", 52),
            ImageFont.truetype("DejaVuSans-Bold.ttf", 60),
            ImageFont.truetype("DejaVuSans.ttf", 44),
            ImageFont.truetype("DejaVuSans.ttf", 40),
        )
    except Exception:
        logger.warning("PLAYBACK: fallback font used")
        default = ImageFont.load_default()
        return (default, default, default, default)


def generate_card(
    cover_image: Image.Image,
    title: str,
    artist: str,
    album: str,
    user_label: str,
) -> Image.Image:
    cover = cover_image.resize((COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0))
    canvas.paste(cover, (0, 0))

    blurred = cover.filter(ImageFilter.GaussianBlur(radius=32)).crop(
        (0, COVER_SIZE - BOTTOM_HEIGHT, COVER_SIZE, COVER_SIZE)
    )
    canvas.paste(blurred, (0, COVER_SIZE))

    overlay = Image.new("RGBA", (CANVAS_WIDTH, BOTTOM_HEIGHT), (0, 0, 0, 110))
    canvas.paste(overlay, (0, COVER_SIZE), overlay)

    draw = ImageDraw.Draw(canvas)
    intro_font, title_font, body_font, body_font_small = _load_fonts()
    text_color = (240, 240, 240)

    lines = [
        (f"🎧 {user_label} está ouvindo…", intro_font),
        (title or "", title_font),
        (artist or "", body_font),
        (album or "", body_font_small),
    ]

    x = 48
    y = COVER_SIZE + 28
    max_width = CANVAS_WIDTH - (x * 2)

    for text, font in lines:
        if not text:
            continue

        clipped = text
        bbox = draw.textbbox((0, 0), clipped, font=font)

        while bbox[2] > max_width and len(clipped) > 1:
            clipped = f"{clipped[:-2]}…"
            bbox = draw.textbbox((0, 0), clipped, font=font)

        draw.text((x, y), clipped, fill=text_color, font=font)
        y += bbox[3] + 10

    return canvas


def get_or_create_image(
    track_id: str,
    cover_url: str,
    title: str,
    artist: str,
    album: str,
    user_label: str,
) -> str | None:
    normalized_track_id = _extract_track_id(track_id)
    if not normalized_track_id or not cover_url:
        logger.error("PLAYBACK: missing track_id or cover_url")
        return None

    normalized_user = (user_label or "").strip() or "unknown"
    cache_key = f"{normalized_track_id}|{normalized_user}"
    output_path = os.path.join(CACHE_DIR, _safe_name(cache_key))

    if os.path.exists(output_path):
        return output_path

    cover_image = _download_cover(cover_url)
    if not cover_image:
        return None

    try:
        card_image = generate_card(
            cover_image, title, artist, album, normalized_user
        )
        save_atomic(card_image, output_path)
        return output_path

    except Exception as exc:
        logger.error("PLAYBACK: image generation failed", exc_info=exc)
        return None