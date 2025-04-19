#!/usr/bin/env python3
"""
publish_data.py

Publishes generated data (images, text, annotations) to the artifacts repository.

Usage:
    python publish_data.py --images path/to/images --text path/to/text --annotations path/to/annotations --output path/to/artifacts_repo

Example:
    python publish_data.py --images ./output/images --text ./output/text --annotations ./output/annotations --output ../artifacts_repo
"""

import sys
import os
import argparse
import shutil
import glob

def copy_files(src_dir, dest_dir, pattern="*"):
    """
    Copy files from source directory to destination directory.
    
    Args:
        src_dir (str): Source directory
        dest_dir (str): Destination directory
        pattern (str): File pattern to match
    """
    os.makedirs(dest_dir, exist_ok=True)
    
    # Find all matching files
    files = glob.glob(os.path.join(src_dir, pattern))
    
    for file_path in files:
        filename = os.path.basename(file_path)
        dest_path = os.path.join(dest_dir, filename)
        
        # Copy file
        shutil.copy2(file_path, dest_path)
        print(f"[+] Copied {file_path} to {dest_path}")

def publish_data(images_dir, text_dir, annotations_dir, output_dir):
    """
    Publish generated data to the artifacts repository.
    
    Args:
        images_dir (str): Directory containing image files
        text_dir (str): Directory containing text JSON files
        annotations_dir (str): Directory containing annotation JSON files
        output_dir (str): Root directory of the artifacts repository
    """
    # Create output directories if they don't exist
    images_output = os.path.join(output_dir, "images")
    text_output = os.path.join(output_dir, "text")
    annotations_output = os.path.join(output_dir, "annotations")
    
    # Copy image files
    if images_dir:
        copy_files(images_dir, images_output, "*.png")
    
    # Copy text JSON files
    if text_dir:
        copy_files(text_dir, text_output, "*.json")
    
    # Copy annotation JSON files
    if annotations_dir:
        copy_files(annotations_dir, annotations_output, "*.json")
    
    print(f"[+] Data published to {output_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish generated data to the artifacts repository")
    parser.add_argument("--images", help="Directory containing image files")
    parser.add_argument("--text", help="Directory containing text JSON files")
    parser.add_argument("--annotations", help="Directory containing annotation JSON files")
    parser.add_argument("--output", required=True, help="Root directory of the artifacts repository")
    
    args = parser.parse_args()
    
    # Ensure at least one source directory is provided
    if not (args.images or args.text or args.annotations):
        print("Error: At least one source directory (--images, --text, or --annotations) must be provided")
        parser.print_help()
        sys.exit(1)
    
    publish_data(args.images, args.text, args.annotations, args.output)