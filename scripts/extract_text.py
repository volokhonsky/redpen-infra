#!/usr/bin/env python3
"""
extract_text.py

Extracts text from a PDF file and generates JSON files with text data.

Usage:
    python extract_text.py path/to/input.pdf path/to/output_dir

Example:
    python extract_text.py textbook.pdf ../artifacts_repo/text/
"""

import sys
import os
import json
import PyPDF2

def extract_text(pdf_path, out_dir):
    """
    Extract text from a PDF file and save as JSON files.

    Args:
        pdf_path (str): Path to the PDF file
        out_dir (str): Directory to save the JSON files
    """
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

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_dir = sys.argv[2]

    extract_text(pdf_path, out_dir)
