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
import fitz  # PyMuPDF

def extract_images(pdf_path, out_dir, zoom=2):
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(zoom, zoom)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        fname = f"page_{i:03d}.png"
        out_path = os.path.join(out_dir, fname)
        pix.save(out_path)
        print(f"[+] Saved {out_path}")
    doc.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    pdf_path = sys.argv[1]
    out_dir  = sys.argv[2]
    zoom     = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    extract_images(pdf_path, out_dir, zoom)