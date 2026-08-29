#!/usr/bin/env python3
"""
process_pdf.py

Main script that orchestrates the entire workflow:
1. Extract images from PDF
2. Extract text from PDF
3. Generate remark templates
4. Publish data to the artifacts repository

Usage:
    python process_pdf.py path/to/input.pdf [--zoom ZOOM] [--output-dir OUTPUT_DIR] [--artifacts-repo ARTIFACTS_REPO]

Example:
    python process_pdf.py textbook.pdf --zoom 2 --output-dir ./output --artifacts-repo ../artifacts_repo
"""

import os
import sys
import glob
import json
import argparse
import tempfile
import shutil
from extract_images import extract_images
from extract_text import extract_text
from publish_data import publish_data


def generate_remark_templates(text_dir, remarks_dir):
    """
    Create an empty remark template (``[]``) for every extracted text page.

    The former standalone ``generate_remarks`` module was removed when
    remarks moved to hand-authored Markdown (see ``remark_converter.py``
    and ``build_website.py``). This inline helper keeps ``process_pdf`` working
    end to end by emitting one empty JSON array per page, ready to be filled in.
    """
    os.makedirs(remarks_dir, exist_ok=True)
    for text_file in sorted(glob.glob(os.path.join(text_dir, "page_*.json"))):
        name = os.path.basename(text_file)
        out_path = os.path.join(remarks_dir, name)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        print(f"[+] Wrote empty remark template {out_path}")

def process_pdf(pdf_path, zoom=2, output_dir=None, artifacts_repo=None):
    """
    Process a PDF file and generate all necessary data.
    
    Args:
        pdf_path (str): Path to the PDF file
        zoom (float): Zoom level for image extraction
        output_dir (str): Directory to save intermediate output
        artifacts_repo (str): Path to the artifacts repository
    """
    # Create temporary directory if output_dir is not provided
    if output_dir is None:
        output_dir = tempfile.mkdtemp()
        temp_dir_created = True
    else:
        os.makedirs(output_dir, exist_ok=True)
        temp_dir_created = False
    
    try:
        # Create subdirectories
        images_dir = os.path.join(output_dir, "images")
        text_dir = os.path.join(output_dir, "text")
        remarks_dir = os.path.join(output_dir, "remarks")
        
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(text_dir, exist_ok=True)
        os.makedirs(remarks_dir, exist_ok=True)
        
        # Step 1: Extract images
        print("\n=== Extracting images ===")
        extract_images(pdf_path, images_dir, zoom)
        
        # Step 2: Extract text
        print("\n=== Extracting text ===")
        extract_text(pdf_path, text_dir)
        
        # Step 3: Generate remark templates
        print("\n=== Generating remark templates ===")
        generate_remark_templates(text_dir, remarks_dir)
        
        # Step 4: Publish data to artifacts repository
        if artifacts_repo:
            print("\n=== Publishing data to artifacts repository ===")
            publish_data(images_dir, text_dir, remarks_dir, artifacts_repo)
        
        print(f"\n=== Processing complete ===")
        print(f"Output directory: {output_dir}")
        if artifacts_repo:
            print(f"Artifacts repository: {artifacts_repo}")
    
    finally:
        # Clean up temporary directory if created
        if temp_dir_created and output_dir:
            shutil.rmtree(output_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a PDF file and generate all necessary data")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--zoom", type=float, default=2.0, help="Zoom level for image extraction")
    parser.add_argument("--output-dir", help="Directory to save intermediate output")
    parser.add_argument("--artifacts-repo", help="Path to the artifacts repository")
    
    args = parser.parse_args()
    
    process_pdf(args.pdf_path, args.zoom, args.output_dir, args.artifacts_repo)