from __future__ import annotations

import os
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont


def generate_card(
    cover_url: str,
    title: str,
    artist: str,
    album: str,
    user: str,
    output_path: str,
) -> str:
    """Gera um card simples de música e salva em ``output_path``."""

    width, height = 1280, 720
    base = Image.new("RGB", (width, height), (16, 18, 24))

    cover = None
    if cover_url:
        try:
            response = requests.get(cover_url, timeout=5)
            response.raise_for_status()
            cover = Image.open(BytesIO(response.content)).convert("RGB")
        except Exception:
            cover = None

    if cover is not None:
        crop_y = int(height * 0.65)
        crop_y = max(1, min(crop_y, cover.height))
        cover_cropped = cover.crop((0, 0, cover.width, crop_y))
        cover_resized = cover_cropped.resize((width, height), Image.Resampling.LANCZOS)
        base.paste(cover_resized, (0, 0))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 120))
    base = Image.alpha_composite(base.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(base)

    try:
        title_font = ImageFont.truetype("Inter.ttf", 56)
        text_font = ImageFont.truetype("Inter.ttf", 36)
    except Exception:
        title_font = ImageFont.load_default()
        text_font = ImageFont.load_default()

    draw.text((56, 470), f"{title}", fill=(255, 255, 255, 255), font=title_font)
    draw.text((56, 550), f"{artist}", fill=(220, 220, 220, 255), font=text_font)
    draw.text((56, 600), f"{album}", fill=(200, 200, 200, 255), font=text_font)
    draw.text((56, 650), f"@{user}", fill=(180, 180, 180, 255), font=text_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    base.convert("RGB").save(output_path, format="PNG")
    return output_path


def get_or_create_image(
    track_id: str,
    cover_url: str,
    title: str,
    artist: str,
    album: str,
    user: str,
) -> str:
    """Retorna card já existente no cache ou cria um novo por ``track_id``."""

    cache_path = os.path.join("cache", f"{track_id}.png")

    if os.path.exists(cache_path):
        return cache_path

    return generate_card(
        cover_url=cover_url,
        title=title,
        artist=artist,
        album=album,
        user=user,
        output_path=cache_path,
    )
