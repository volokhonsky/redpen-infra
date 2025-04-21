#!/usr/bin/env python3

import os
import json
import PyPDF2
from pdf2image import convert_from_path
import argparse

def extract_text(pdf_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        num_pages = len(reader.pages)

        for i in range(num_pages):
            page_num = f"{i+1:03d}"
            page = reader.pages[i]
            text = page.extract_text()

            # Split text into lines and create blocks
            lines = text.split('\n')
            text_blocks = []

            for j, line in enumerate(lines):
                if line.strip():
                    # Approximate bounding box (PyPDF2 doesn't provide position info)
                    # We'll use dummy values that will be replaced with real ones when displayed
                    text_blocks.append({
                        "id": f"page_{page_num}_line{j:03d}",
                        "text": line,
                        "bbox": [0, j*20, 500, j*20+15]  # Dummy values
                    })

            # Save as JSON
            out_file = os.path.join(out_dir, f"page_{page_num}.json")
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(text_blocks, f, ensure_ascii=False, indent=2)

            print(f"[+] Saved text data to {out_file}")

    return num_pages

def extract_images(pdf_path, out_dir, zoom=2.0):
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

    return len(images)

def create_annotations(pdf_path, out_dir, base_id, logical_start=1, physical_start=1):
    os.makedirs(out_dir, exist_ok=True)

    # Get total pages
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        total_pages = len(reader.pages)

    for i in range(1, total_pages + 1):
        page_id = f"page_{i:03d}.md"
        path = os.path.join(out_dir, page_id)
        open(path, "w", encoding="utf-8").close()

    meta = {
        "id": base_id,
        "pageNumbering": {
            "physicalStart": physical_start,
            "logicalStart": logical_start
        },
        "totalPages": total_pages
    }

    meta_path = os.path.join(out_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"[+] Created annotations in {out_dir} and meta.json with {total_pages} pages")

def main():
    parser = argparse.ArgumentParser(description="Extract text and images from a PDF file.")
    parser.add_argument("pdf_path", help="Path to input PDF file")
    parser.add_argument("output_dir", help="Root output directory (will contain 'text/' and 'images/')")
    parser.add_argument("--zoom", type=float, default=2.0, help="Zoom factor for images (default: 2.0)")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"[!] PDF file not found: {args.pdf_path}")
        return

    text_dir = os.path.join(args.output_dir, "text")
    images_dir = os.path.join(args.output_dir, "images")
    annotations_dir = os.path.join(args.output_dir, "annotations")

    # Extract text
    extract_text(args.pdf_path, text_dir)

    # Extract images
    extract_images(args.pdf_path, images_dir, args.zoom)

    # Create annotations
    base_id = os.path.basename(os.path.normpath(args.output_dir))
    create_annotations(args.pdf_path, annotations_dir, base_id)

    print("[+] All processing complete!")

if __name__ == "__main__":
    main()
