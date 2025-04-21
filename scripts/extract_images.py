#!/usr/bin/env python3
"""
extract_images.py

Нарезает входной PDF на по‑страничные PNG‑файлы с заданным масштабом.

Usage:
    python extract_images.py path/to/input.pdf path/to/output_dir [zoom]

Пример:
    python extract_images.py textbook.pdf images/ 2
"""

import sys, os
from pdf2image import convert_from_path

def extract_images(pdf_path, out_dir, zoom=2):
    os.makedirs(out_dir, exist_ok=True)

    # Calculate DPI based on zoom factor (default PDF DPI is 72)
    dpi = int(72 * zoom)

    # Convert PDF to images
    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        output_folder=None,  # Don't save directly, we'll handle that
        fmt="png",
        thread_count=4  # Use multiple threads for faster conversion
    )

    # Save each image
    for i, image in enumerate(images, start=1):
        fname = f"page_{i:03d}.png"
        out_path = os.path.join(out_dir, fname)
        image.save(out_path)
        print(f"[+] Saved {out_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    pdf_path = sys.argv[1]
    out_dir  = sys.argv[2]
    zoom     = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    extract_images(pdf_path, out_dir, zoom)
