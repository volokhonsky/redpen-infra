#!/usr/bin/env python3

import os
import re
import glob

def rename_files_in_directory(directory):
    """
    Rename files in the specified directory from page_XXX.ext to page_YYY.ext
    where YYY is XXX-1 (e.g., page_001.md becomes page_000.md)
    """
    # Get all files matching the pattern page_*.md
    pattern = os.path.join(directory, "page_*.md")
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
                # If the destination file exists but is empty, and the source file has content,
                # remove the destination file and proceed with renaming
                if os.path.getsize(new_file_path) == 0 and os.path.getsize(file_path) > 0:
                    print(f"Removing empty file {new_file_path} and renaming {file_path} to it")
                    os.remove(new_file_path)
                else:
                    print(f"Skipping {file_path} as {new_file_path} already exists")
                    continue

            # Rename the file
            print(f"Renaming {file_path} to {new_file_path}")
            os.rename(file_path, new_file_path)

def main():
    # Directory to process
    directory = "redpen-content/medinsky11klass/annotations"

    print(f"Processing directory: {directory}")
    rename_files_in_directory(directory)
    print(f"Finished processing directory: {directory}")

if __name__ == "__main__":
    main()
