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
import fitz  # PyMuPDF

def extract_text(pdf_path, out_dir):
    """
    Extract text from a PDF file and save as JSON files.
    
    Args:
        pdf_path (str): Path to the PDF file
        out_dir (str): Directory to save the JSON files
    """
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    
    for i, page in enumerate(doc, start=1):
        page_num = f"{i:03d}"
        text_blocks = []
        
        # Extract text blocks
        blocks = page.get_text("dict")["blocks"]
        for j, block in enumerate(blocks):
            if "lines" in block:
                for line in block["lines"]:
                    text = ""
                    for span in line["spans"]:
                        text += span["text"]
                    
                    if text.strip():
                        text_blocks.append({
                            "id": f"page_{page_num}_line{j:03d}",
                            "text": text,
                            "bbox": [
                                line["bbox"][0],
                                line["bbox"][1],
                                line["bbox"][2],
                                line["bbox"][3]
                            ]
                        })
        
        # Save as JSON
        out_file = os.path.join(out_dir, f"page_{page_num}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(text_blocks, f, ensure_ascii=False, indent=2)
        
        print(f"[+] Saved text data to {out_file}")
    
    doc.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    out_dir = sys.argv[2]
    
    extract_text(pdf_path, out_dir)