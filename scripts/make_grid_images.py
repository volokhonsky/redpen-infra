#!/usr/bin/env python3
"""Накладывает координатную сетку на PNG страниц учебника.

Координаты на сетке соответствуют пиксельным координатам исходного изображения
(тем самым, что пишутся в target: [x, y] замечаний). Страница помещается на
расширенный холст с полями, подписи осей — в полях, поэтому контент не перекрывается.

Использование:
    python3 scripts/make_grid_images.py <content_dir> [page_from] [page_to]

Пример:
    python3 scripts/make_grid_images.py redpen-content/medinsky11klass 6 20
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MARGIN = 46          # поля под подписи
STEP_MINOR = 50      # тонкая сетка
STEP_MAJOR = 100     # жирная сетка с подписями
COLOR_MINOR = (255, 120, 120, 60)
COLOR_MAJOR = (220, 30, 30, 130)
COLOR_LABEL = (170, 0, 0)


def load_font(size: int):
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_grid(src: Path, dst: Path) -> None:
    page = Image.open(src).convert("RGB")
    w, h = page.size
    canvas = Image.new("RGB", (w + 2 * MARGIN, h + 2 * MARGIN), (255, 255, 255))
    canvas.paste(page, (MARGIN, MARGIN))

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font = load_font(16)

    for x in range(0, w + 1, STEP_MINOR):
        major = x % STEP_MAJOR == 0
        d.line(
            [(MARGIN + x, MARGIN), (MARGIN + x, MARGIN + h)],
            fill=COLOR_MAJOR if major else COLOR_MINOR,
            width=1,
        )
        if major:
            d.text((MARGIN + x, MARGIN - 20), str(x), fill=COLOR_LABEL, font=font, anchor="ms")
            d.text((MARGIN + x, MARGIN + h + 20), str(x), fill=COLOR_LABEL, font=font, anchor="ms")

    for y in range(0, h + 1, STEP_MINOR):
        major = y % STEP_MAJOR == 0
        d.line(
            [(MARGIN, MARGIN + y), (MARGIN + w, MARGIN + y)],
            fill=COLOR_MAJOR if major else COLOR_MINOR,
            width=1,
        )
        if major:
            d.text((MARGIN - 6, MARGIN + y), str(y), fill=COLOR_LABEL, font=font, anchor="rm")
            d.text((MARGIN + w + 6, MARGIN + y), str(y), fill=COLOR_LABEL, font=font, anchor="lm")

    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst)


def main() -> None:
    content = Path(sys.argv[1])
    page_from = int(sys.argv[2]) if len(sys.argv) > 2 else None
    page_to = int(sys.argv[3]) if len(sys.argv) > 3 else None

    src_dir = content / "images"
    dst_dir = content / "images_with_grid"
    count = 0
    for src in sorted(src_dir.glob("page_*.png")):
        num_str = src.stem.split("_")[1]
        try:
            num = int(num_str)
        except ValueError:
            num = None
        if page_from is not None and (num is None or num < page_from):
            continue
        if page_to is not None and (num is None or num > page_to):
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue
        make_grid(src, dst)
        count += 1
        print(f"{src.name} -> {dst}")
    print(f"Done: {count} images")


if __name__ == "__main__":
    main()
