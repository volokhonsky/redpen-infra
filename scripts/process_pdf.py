#!/usr/bin/env python3
"""
process_pdf.py

Main script that orchestrates the entire workflow:
1. Extract images from PDF
2. Extract text from PDF
3. Generate annotation templates
4. Publish data to the artifacts repository

Usage:
    python process_pdf.py path/to/input.pdf [--zoom ZOOM] [--output-dir OUTPUT_DIR] [--artifacts-repo ARTIFACTS_REPO]

Example:
    python process_pdf.py textbook.pdf --zoom 2 --output-dir ./output --artifacts-repo ../artifacts_repo
"""

import os
import sys
import argparse
import tempfile
import shutil
from extract_images import extract_images
from extract_text import extract_text
from generate_annotations import generate_annotations
from publish_data import publish_data

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
        annotations_dir = os.path.join(output_dir, "annotations")
        
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(text_dir, exist_ok=True)
        os.makedirs(annotations_dir, exist_ok=True)
        
        # Step 1: Extract images
        print("\n=== Extracting images ===")
        extract_images(pdf_path, images_dir, zoom)
        
        # Step 2: Extract text
        print("\n=== Extracting text ===")
        extract_text(pdf_path, text_dir)
        
        # Step 3: Generate annotation templates
        print("\n=== Generating annotation templates ===")
        generate_annotations(text_dir, annotations_dir)
        
        # Step 4: Publish data to artifacts repository
        if artifacts_repo:
            print("\n=== Publishing data to artifacts repository ===")
            publish_data(images_dir, text_dir, annotations_dir, artifacts_repo)
        
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