from __future__ import annotations

import html
import os
from pathlib import Path
import tempfile

from playwright.async_api import async_playwright


_TEMPLATE_PATH = Path(__file__).resolve().parent / "template.html"


def _replace(template: str, payload: dict[str, str]) -> str:
    rendered = template
    for key, value in payload.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", html.escape(value, quote=True))
    return rendered


async def render_image(payload: dict[str, str]) -> bytes:
    with _TEMPLATE_PATH.open(encoding="utf-8") as template_file:
        template = template_file.read()

    html_content = _replace(template, payload)

    temp_path = ""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as temp_file:
        temp_file.write(html_content)
        temp_path = temp_file.name

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page(viewport={"width": 1080, "height": 1350})
            await page.goto(f"file://{temp_path}", wait_until="networkidle")
            image = await page.screenshot(type="png")
            await browser.close()
            return image
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
