from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "assets" / "icon" / "langbai-icon-source.png"
OUTPUT_ROOT = PROJECT_ROOT / "assets" / "icon"
PNG_ROOT = OUTPUT_ROOT / "png"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def rounded_square(source: Image.Image, size: int) -> Image.Image:
    image = ImageOps.fit(source.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    radius = max(2, round(size * 0.14))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"Icon source not found: {SOURCE}")
    PNG_ROOT.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as source:
        icons = [rounded_square(source, size) for size in SIZES]
    for size, icon in zip(SIZES, icons, strict=True):
        icon.save(PNG_ROOT / f"icon-{size}.png", optimize=True)
    icons[-1].save(OUTPUT_ROOT / "langbai.ico", format="ICO", sizes=[(size, size) for size in SIZES])
    icons[-1].save(OUTPUT_ROOT / "langbai-icon.png", optimize=True)


if __name__ == "__main__":
    main()
