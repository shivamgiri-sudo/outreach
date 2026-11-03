"""Regenerate the Chrome assistant icons (blue rounded square, white arrow). Needs Pillow (not an app dependency).

    python3 scripts/make_extension_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "extensions" / "linkedin-assistant" / "icons"


def make(n: int) -> None:
    scale = 4
    size = n * scale
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=(27, 95, 214, 255))
    w = max(2, int(size * 0.09))
    mid = size / 2
    d.line((size * 0.24, mid, size * 0.74, mid), fill="white", width=w)
    d.line((size * 0.50, size * 0.26, size * 0.76, mid), fill="white", width=w)
    d.line((size * 0.50, size * 0.74, size * 0.76, mid), fill="white", width=w)
    im.resize((n, n), Image.LANCZOS).save(OUT / f"icon{n}.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for n in (16, 48, 128):
        make(n)
