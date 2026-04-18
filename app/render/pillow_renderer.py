from __future__ import annotations

import io

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350
COVER_SIZE = 1080
BOTTOM_HEIGHT = CANVAS_HEIGHT - COVER_SIZE
PADDING_X = 72


def _load_fonts() -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont]:
    try:
        return (
            ImageFont.truetype("DejaVuSans.ttf", 40),
            ImageFont.truetype("DejaVuSans-Bold.ttf", 66),
            ImageFont.truetype("DejaVuSans.ttf", 44),
            ImageFont.truetype("DejaVuSans.ttf", 34),
        )
    except Exception:
        default_font = ImageFont.load_default()
        return (default_font, default_font, default_font, default_font)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    normalized = (text or "").strip()
    if not normalized:
        return []

    words = normalized.split()
    lines: list[str] = []
    current = words[0]

    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines - 1:
            break

    lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if len(lines) == max_lines:
        last = lines[-1]
        while last and draw.textbbox((0, 0), f"{last}…", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = f"{last}…" if last else "…"

    return lines


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    y_start: int,
    lines: list[str],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int] | tuple[int, int, int],
) -> int:
    y = y_start
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        line_width = box[2] - box[0]
        line_height = box[3] - box[1]
        x = (CANVAS_WIDTH - line_width) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + 10
    return y


async def _download_cover(cover_url: str) -> Image.Image:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(cover_url)
        response.raise_for_status()
    image = Image.open(io.BytesIO(response.content))
    image.load()
    return image.convert("RGB")


async def render_image(payload: dict[str, str]) -> bytes:
    cover_url = str(payload.get("COVER") or "").strip()
    if not cover_url:
        raise RuntimeError("Missing cover url")

    cover_image = await _download_cover(cover_url)

    cover_square = cover_image.resize((COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    canvas.paste(cover_square, (0, 0))

    blurred = cover_square.filter(ImageFilter.GaussianBlur(radius=36)).crop(
        (0, COVER_SIZE - BOTTOM_HEIGHT, CANVAS_WIDTH, COVER_SIZE)
    )
    canvas.paste(blurred, (0, COVER_SIZE))

    gradient = Image.new("L", (1, BOTTOM_HEIGHT))
    for y in range(BOTTOM_HEIGHT):
        opacity = int(130 + (95 * y / max(1, BOTTOM_HEIGHT - 1)))
        gradient.putpixel((0, y), opacity)
    gradient = gradient.resize((CANVAS_WIDTH, BOTTOM_HEIGHT))
    gradient_overlay = Image.new("RGBA", (CANVAS_WIDTH, BOTTOM_HEIGHT), (0, 0, 0, 0))
    gradient_overlay.putalpha(gradient)
    canvas.paste(gradient_overlay, (0, COVER_SIZE), gradient_overlay)

    draw = ImageDraw.Draw(canvas)
    intro_font, title_font, artist_font, album_font = _load_fonts()
    text_width = CANVAS_WIDTH - (PADDING_X * 2)

    y = COVER_SIZE + 24

    intro_lines = _wrap_text(
        draw,
        f"🎧 {payload.get('USER', '').strip()} está ouvindo…",
        intro_font,
        text_width,
        max_lines=1,
    )
    y = _draw_centered_lines(draw, y, intro_lines, intro_font, (255, 255, 255, 235))
    y += 8

    title_lines = _wrap_text(
        draw,
        str(payload.get("TRACK") or ""),
        title_font,
        text_width,
        max_lines=2,
    )
    y = _draw_centered_lines(draw, y, title_lines, title_font, (255, 255, 255, 255))
    y += 4

    artist_lines = _wrap_text(
        draw,
        str(payload.get("ARTIST") or ""),
        artist_font,
        text_width,
        max_lines=1,
    )
    y = _draw_centered_lines(draw, y, artist_lines, artist_font, (255, 255, 255, 245))
    y += 2

    album_lines = _wrap_text(
        draw,
        str(payload.get("ALBUM") or ""),
        album_font,
        text_width,
        max_lines=1,
    )
    _draw_centered_lines(draw, y, album_lines, album_font, (255, 255, 255, 175))

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
