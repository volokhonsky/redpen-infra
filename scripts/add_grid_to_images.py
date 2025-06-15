#!/usr/bin/env python3
"""
Script to add coordinate grid to images.

This script adds a coordinate grid to images with thin gray lines every 100 pixels
and labels every 200 pixels. The grid starts from the top-left corner (0,0).
"""

import os
import sys
from PIL import Image, ImageDraw, ImageFont

def add_grid_to_image(input_path, output_path):
    """
    Add a coordinate grid to an image.
    
    Args:
        input_path (str): Path to the input image
        output_path (str): Path where the output image will be saved
    """
    try:
        # Open the image
        img = Image.open(input_path)
        
        # Create a drawing object
        draw = ImageDraw.Draw(img)
        
        # Get image dimensions
        width, height = img.size
        
        # Define grid properties
        grid_spacing = 100  # Grid lines every 100px
        label_spacing = 200  # Labels every 200px
        grid_color = (200, 200, 200)  # Light gray
        label_color = (100, 100, 100)  # Darker gray for labels
        
        # Try to load a small font
        try:
            # Try to find a system font
            font = ImageFont.truetype("Arial", 10)
        except IOError:
            # Fallback to default font
            font = ImageFont.load_default()
        
        # Draw vertical grid lines
        for x in range(0, width, grid_spacing):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
            
            # Add labels every 200px
            if x % label_spacing == 0:
                label = f"{x}px"
                draw.text((x + 2, 2), label, fill=label_color, font=font)
        
        # Draw horizontal grid lines
        for y in range(0, height, grid_spacing):
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)
            
            # Add labels every 200px
            if y % label_spacing == 0:
                label = f"{y}px"
                draw.text((2, y + 2), label, fill=label_color, font=font)
        
        # Save the image
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path)
        print(f"Processed: {input_path} -> {output_path}")
        return True
    
    except Exception as e:
        print(f"Error processing {input_path}: {e}", file=sys.stderr)
        return False

def process_directory(input_dir, output_dir):
    """
    Process all images in a directory.
    
    Args:
        input_dir (str): Directory containing input images
        output_dir (str): Directory where output images will be saved
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Get all files in the input directory
    files = os.listdir(input_dir)
    
    # Filter for image files (assuming PNG files)
    image_files = [f for f in files if f.lower().endswith('.png')]
    
    # Process each image
    success_count = 0
    for image_file in image_files:
        input_path = os.path.join(input_dir, image_file)
        output_path = os.path.join(output_dir, image_file)
        
        if add_grid_to_image(input_path, output_path):
            success_count += 1
    
    print(f"Processed {success_count} of {len(image_files)} images")

if __name__ == "__main__":
    # Define input and output directories
    input_dir = "redpen-content/medinsky11klass/images"
    output_dir = "redpen-content/medinsky11klass/images_with_grid"
    
    # Process all images
    process_directory(input_dir, output_dir)
    
    print(f"Grid added to images. Results saved in {output_dir}")