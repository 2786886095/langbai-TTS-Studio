from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a same-viewport UI comparison image.")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    baseline = Image.open(args.baseline).convert("RGB")
    current = Image.open(args.current).convert("RGB")
    if baseline.size != current.size:
        raise SystemExit(f"Viewport mismatch: {baseline.size} != {current.size}")

    label_height = 40
    gutter = 12
    width, height = baseline.size
    canvas = Image.new("RGB", (width * 2 + gutter, height + label_height), "#E2E8F0")
    canvas.paste(baseline, (0, label_height))
    canvas.paste(current, (width + gutter, label_height))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=16)
    draw.rectangle((0, 0, width, label_height), fill="#0F2742")
    draw.rectangle((width + gutter, 0, width * 2 + gutter, label_height), fill="#0F2742")
    draw.text((16, 11), "BASELINE", fill="white", font=font)
    draw.text((width + gutter + 16, 11), "CURRENT", fill="white", font=font)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output, optimize=True)


if __name__ == "__main__":
    main()
