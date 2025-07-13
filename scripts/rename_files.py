#!/usr/bin/env python3

import os
import re
import glob

def rename_files_in_directory(directory):
    """
    Rename files in the specified directory from page_XXX.ext to page_YYY.ext
    where YYY is XXX-1 (e.g., page_001.png becomes page_000.png)
    """
    # Get all files matching the pattern page_*.ext
    pattern = os.path.join(directory, "page_*.???")
    files = glob.glob(pattern)
    
    # Sort files in reverse order to avoid conflicts
    files.sort(reverse=True)
    
    for file_path in files:
        # Extract the file name and extension
        dir_name, file_name = os.path.split(file_path)
        
        # Use regex to extract the number part
        match = re.match(r'page_(\d+)\.(.+)', file_name)
        if match:
            number_str = match.group(1)
            extension = match.group(2)
            
            # Convert to integer, subtract 1, and format back to 3-digit string
            number = int(number_str)
            new_number = number - 1
            new_number_str = f"{new_number:03d}"
            
            # Create the new file name
            new_file_name = f"page_{new_number_str}.{extension}"
            new_file_path = os.path.join(dir_name, new_file_name)
            
            # Rename the file
            print(f"Renaming {file_path} to {new_file_path}")
            os.rename(file_path, new_file_path)

def main():
    # Base directory
    base_dir = "redpen-content/medinsky11klass"
    
    # Directories to process
    directories = [
        os.path.join(base_dir, "images"),
        os.path.join(base_dir, "annotations"),
        os.path.join(base_dir, "images_with_grid"),
        os.path.join(base_dir, "text")
    ]
    
    # Process each directory
    for directory in directories:
        print(f"Processing directory: {directory}")
        rename_files_in_directory(directory)
        print(f"Finished processing directory: {directory}")

if __name__ == "__main__":
    main()