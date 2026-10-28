"""Screenshot colour analysis with Pillow (no vision model required).

DeepSeek's API is text-only, so the palette is extracted programmatically from
the post thumbnail. We quantize to a handful of colours, drop near-white and
near-black (they carry little style information), and keep the largest share
colour as the background guess.
"""

from __future__ import annotations

import colorsys
import logging
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..http import HttpClient
from ..models import Palette, PaletteColor, Post

log = logging.getLogger(__name__)

TOP_COLORS = 5
QUANTIZE_COLORS = 8
IMAGE_TTL = 30 * 86400  # thumbnails are immutable in practice


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _is_boring(rgb: tuple[int, int, int]) -> bool:
    """Near-white and near-black pixels rarely describe a rice's palette."""
    _h, _s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if v > 0.95 and _s < 0.10:
        return True
    return v < 0.05


def quantize_palette(image: Image.Image, top_n: int = TOP_COLORS) -> Palette:
    rgb_image = image.convert("RGB")
    if not rgb_image.width or not rgb_image.height:
        return Palette()
    quantized = rgb_image.quantize(colors=QUANTIZE_COLORS, method=Image.Quantize.MEDIANCUT)
    palette_bytes = quantized.getpalette() or []
    counts = quantized.getcolors(maxcolors=QUANTIZE_COLORS * 4) or []
    total = sum(count for count, _ in counts) or 1

    entries: list[tuple[float, tuple[int, int, int]]] = []
    for count, index in counts:
        offset = index * 3
        if offset + 2 >= len(palette_bytes):
            continue
        rgb = (palette_bytes[offset], palette_bytes[offset + 1], palette_bytes[offset + 2])
        entries.append((count / total, rgb))
    entries.sort(key=lambda item: item[0], reverse=True)
    if not entries:
        return Palette()

    _bg_share, bg_rgb = entries[0]
    interesting = [(share, rgb) for share, rgb in entries if not _is_boring(rgb)]
    if not interesting:  # monochrome rice (pure black/white) still deserves a swatch
        interesting = entries[:1]

    # Style colours first; fill the remaining slots with the dropped neutrals so a
    # black/white rice still shows its white accent instead of one grey swatch.
    ordered = interesting + [entry for entry in entries if entry not in interesting]

    colors: list[PaletteColor] = []
    seen: set[str] = set()
    for share, rgb in ordered:
        hex_value = _hex(rgb)
        if hex_value in seen:
            continue
        seen.add(hex_value)
        colors.append(PaletteColor(hex=hex_value, share=share))
        if len(colors) >= top_n:
            break

    return Palette(colors=colors, dark=_luminance(bg_rgb) < 0.35, bg_hex=_hex(bg_rgb))


def extract_palette(data: bytes, top_n: int = TOP_COLORS) -> Palette:
    try:
        with Image.open(BytesIO(data)) as image:
            return quantize_palette(image, top_n=top_n)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        log.warning("could not read image for palette: %s", exc)
        return Palette()


def fetch_palette(http: HttpClient, post: Post, top_n: int = TOP_COLORS) -> Palette:
    if not post.image_url:
        return Palette()
    try:
        data = http.get(post.image_url, ttl=IMAGE_TTL)
    except Exception as exc:  # noqa: BLE001 - images are nice-to-have, never fatal
        log.warning("thumbnail download failed for %s: %s", post.id, exc)
        return Palette()
    return extract_palette(data, top_n=top_n)
