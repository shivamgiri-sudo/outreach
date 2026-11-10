"""Convert guide screenshots (PNG) to WebP under 150 KB each: python3 tools/guide_webp.py <png dir> web/public/guide"""
import sys
from pathlib import Path

from PIL import Image

src, dest = Path(sys.argv[1]), Path(sys.argv[2])
dest.mkdir(parents=True, exist_ok=True)
for png in sorted(src.glob("*.png")):
    img = Image.open(png).convert("RGB")
    for q in (82, 74, 66, 58, 50, 40):
        out = dest / f"{png.stem}.webp"
        img.save(out, "WEBP", quality=q, method=6)
        if out.stat().st_size <= 150 * 1024:
            break
    print(out.name, out.stat().st_size // 1024, "KB q", q)
