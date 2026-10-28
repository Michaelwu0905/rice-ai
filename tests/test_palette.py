from __future__ import annotations

from PIL import Image

from rice_ai.enrich.palette import extract_palette, quantize_palette
from rice_ai.models import Palette


def _two_tone(bg=(18, 18, 28), accent=(215, 25, 33), accent_size=20, size=100) -> Image.Image:
    image = Image.new("RGB", (size, size), bg)
    for x in range(accent_size):
        for y in range(accent_size):
            image.putpixel((x, y), accent)
    return image


def test_solid_black_is_dark():
    palette = quantize_palette(Image.new("RGB", (12, 12), (0, 0, 0)))
    assert palette.dark is True
    assert palette.bg_hex == "#000000"
    assert [c.hex for c in palette.colors] == ["#000000"]


def test_solid_white_is_light():
    palette = quantize_palette(Image.new("RGB", (12, 12), (255, 255, 255)))
    assert palette.dark is False
    assert palette.colors[0].hex == "#ffffff"


def test_accent_colour_comes_first_when_bg_is_near_black():
    palette = quantize_palette(_two_tone(bg=(8, 8, 8)))
    hexes = [c.hex for c in palette.colors]
    assert "#d71921" in hexes
    assert hexes[0] == "#d71921"  # style colour beats the boring background
    assert palette.bg_hex == "#080808"
    assert palette.dark is True


def test_dominant_colour_leads_when_bg_is_not_boring():
    palette = quantize_palette(_two_tone(bg=(18, 18, 28)))
    assert palette.colors[0].hex == "#12121c"


def test_neutrals_are_kept_as_filler():
    image = Image.new("RGB", (60, 60), (10, 10, 10))
    for x in range(20):
        for y in range(20):
            image.putpixel((x, y), (250, 250, 250))
    palette = quantize_palette(image)
    assert "#fafafa" in [c.hex for c in palette.colors]
    assert len(palette.colors) <= 5


def test_shares_are_fractions_and_sorted():
    palette = quantize_palette(_two_tone())
    shares = [c.share for c in palette.colors]
    assert all(0 < s <= 1 for s in shares)
    assert shares == sorted(shares, reverse=True) or len(shares) == 1


def test_real_thumbnail_extracts_palette(thumb_bytes):
    palette = extract_palette(thumb_bytes)
    assert len(palette.colors) == 5
    assert palette.dark is True
    assert all(c.hex.startswith("#") and len(c.hex) == 7 for c in palette.colors)


def test_garbage_bytes_return_empty_palette():
    palette = extract_palette(b"definitely not an image")
    assert palette == Palette()
    assert palette.colors == []


def test_palette_roundtrip():
    original = Palette.from_dict(
        {
            "colors": [{"hex": "#112233", "share": 0.5}, {"hex": "#445566", "share": 0.25}],
            "dark": False,
            "bg_hex": "#ffffff",
        }
    )
    restored = Palette.from_dict(original.as_dict())
    assert restored.hexes == ["#112233", "#445566"]
    assert restored.dark is False
    assert restored.bg_hex == "#ffffff"
    assert Palette.from_dict(None) == Palette()
