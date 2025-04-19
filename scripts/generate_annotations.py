#!/usr/bin/env python3
"""
generate_annotations.py

Generates annotation template JSON files based on text JSON files.

Usage:
    python generate_annotations.py path/to/text_dir path/to/output_dir

Example:
    python generate_annotations.py ../artifacts_repo/text/ ../artifacts_repo/annotations/
"""

import sys
import os
import json
import glob

def generate_annotations(text_dir, out_dir):
    """
    Generate annotation template JSON files based on text JSON files.
    
    Args:
        text_dir (str): Directory containing text JSON files
        out_dir (str): Directory to save the annotation JSON files
    """
    os.makedirs(out_dir, exist_ok=True)
    
    # Find all text JSON files
    text_files = glob.glob(os.path.join(text_dir, "page_*.json"))
    
    for text_file in text_files:
        # Extract page number from filename
        filename = os.path.basename(text_file)
        page_num = filename.split("_")[1].split(".")[0]
        
        # Load text data
        with open(text_file, "r", encoding="utf-8") as f:
            text_data = json.load(f)
        
        # Create empty annotations list
        annotations = []
        
        # For demonstration, create a template annotation for the first text block if available
        if text_data and len(text_data) > 0:
            first_block = text_data[0]
            annotations.append({
                "id": f"ann-page{page_num}-1",
                "targetBlock": f"page{page_num}_line000",
                "text": "Add your annotation here",
                "annType": "main",
                "coords": [
                    int((first_block["bbox"][0] + first_block["bbox"][2]) / 2),
                    int((first_block["bbox"][1] + first_block["bbox"][3]) / 2)
                ]
            })
        
        # Save as JSON
        out_file = os.path.join(out_dir, f"page_{page_num}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(annotations, f, ensure_ascii=False, indent=2)
        
        print(f"[+] Saved annotation template to {out_file}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    text_dir = sys.argv[1]
    out_dir = sys.argv[2]
    
    generate_annotations(text_dir, out_dir)