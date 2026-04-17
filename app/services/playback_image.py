from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile

import httpx
from PIL import Image, ImageDraw, ImageOps

logger = logging.getLogger(__name__)
CACHE_DIR = "app/cache/playback"


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


def _safe_name(track_id: str) -> str:
    hashed = hashlib.sha256(track_id.encode("utf-8")).hexdigest()
    return f"{hashed}.jpg"


def _extract_track_id(track_id: str) -> str:
    return (track_id or "").strip()


def _download_cover(cover_url: str) -> Image.Image | None:
    try:
        response = httpx.get(cover_url, timeout=10.0)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image.load()
        return image.convert("RGB")
    except Exception as exc:
        logger.warning("Failed to download Spotify cover", exc_info=exc)
        return None


def generate_card(cover_image: Image.Image, title: str, artist: str, album: str) -> Image.Image:
    base = cover_image.resize((640, 640), Image.Resampling.LANCZOS)
    card = ImageOps.expand(base, border=(0, 0, 0, 160), fill=(18, 18, 18))

    draw = ImageDraw.Draw(card)
    text = f"{title}\n{artist}\n{album}"
    draw.multiline_text((24, 662), text, fill=(240, 240, 240), spacing=6)
    return card


def get_or_create_image(
    track_id: str,
    cover_url: str,
    title: str,
    artist: str,
    album: str,
) -> str | None:
    normalized_track_id = _extract_track_id(track_id)
    if not normalized_track_id or not cover_url:
        return None

    output_path = os.path.join(CACHE_DIR, _safe_name(normalized_track_id))
    if os.path.exists(output_path):
        return output_path

    cover_image = _download_cover(cover_url)
    if not cover_image:
        return None

    try:
        card_image = generate_card(cover_image, title, artist, album)
        save_atomic(card_image, output_path)
        return output_path
    except Exception as exc:
        logger.warning("Failed generating playback card", exc_info=exc)
        return None
