#!/usr/bin/env python3

import os
import re
import glob
import shutil

def clean_directory(directory):
    """
    Remove any files with negative numbers in their names
    """
    pattern = os.path.join(directory, "page_-*.???")
    files = glob.glob(pattern)
    
    for file_path in files:
        print(f"Removing {file_path}")
        os.remove(file_path)

def copy_files_from_publish_to_content(source_dir, dest_dir):
    """
    Copy files from the publish directory to the content directory
    """
    # Create the destination directory if it doesn't exist
    os.makedirs(dest_dir, exist_ok=True)
    
    # Get all files matching the pattern page_*.ext in the source directory
    pattern = os.path.join(source_dir, "page_*.???")
    files = glob.glob(pattern)
    
    for file_path in files:
        # Extract the file name
        _, file_name = os.path.split(file_path)
        
        # Create the destination path
        dest_path = os.path.join(dest_dir, file_name)
        
        # Copy the file
        print(f"Copying {file_path} to {dest_path}")
        shutil.copy2(file_path, dest_path)

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
            
            # Skip files that would have negative numbers
            if new_number < 0:
                continue
                
            new_number_str = f"{new_number:03d}"
            
            # Create the new file name
            new_file_name = f"page_{new_number_str}.{extension}"
            new_file_path = os.path.join(dir_name, new_file_name)
            
            # Skip if the destination file already exists
            if os.path.exists(new_file_path):
                print(f"Skipping {file_path} as {new_file_path} already exists")
                continue
                
            # Rename the file
            print(f"Renaming {file_path} to {new_file_path}")
            os.rename(file_path, new_file_path)

def main():
    # Base directories
    content_base_dir = "redpen-content/medinsky11klass"
    publish_base_dir = "redpen-publish/medinsky11klass"
    
    # Directories to process
    content_directories = [
        os.path.join(content_base_dir, "images"),
        os.path.join(content_base_dir, "annotations"),
        os.path.join(content_base_dir, "images_with_grid"),
        os.path.join(content_base_dir, "text")
    ]
    
    publish_directories = [
        os.path.join(publish_base_dir, "images"),
        os.path.join(publish_base_dir, "annotations"),
        os.path.join(publish_base_dir, "text")
    ]
    
    # First, clean up any files with negative numbers
    for directory in content_directories:
        print(f"Cleaning directory: {directory}")
        clean_directory(directory)
    
    # Then, copy files from publish to content
    for i, publish_dir in enumerate(publish_directories):
        if os.path.exists(publish_dir):
            content_dir = content_directories[i]
            print(f"Copying files from {publish_dir} to {content_dir}")
            copy_files_from_publish_to_content(publish_dir, content_dir)
    
    # Finally, rename the files in the content directories
    for directory in content_directories:
        print(f"Processing directory: {directory}")
        rename_files_in_directory(directory)
        print(f"Finished processing directory: {directory}")

if __name__ == "__main__":
    main()