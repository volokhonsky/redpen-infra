#!/bin/bash

# Script to pull changes, build the website, and push changes
# This script handles the main repository and both submodules (redpen-content and redpen-publish)

# Exit on error
set -e

# Get the root directory of the project
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Updating All Repositories ==="

# Function to pull changes for a repository
pull_repo() {
    local repo_path="$1"
    local repo_name="$2"
    
    echo "Pulling changes for repository: $repo_name"
    
    # Change to the repository directory
    cd "$repo_path"
    
    # Pull changes
    echo "Pulling changes in $repo_name..."
    git pull
    
    # Return to the root directory
    cd "$ROOT_DIR"
}

# Pull changes for the main repository
pull_repo "$ROOT_DIR" "main repository"

# Pull changes for redpen-content submodule
pull_repo "$ROOT_DIR/redpen-content" "redpen-content"

# Pull changes for redpen-publish submodule
pull_repo "$ROOT_DIR/redpen-publish" "redpen-publish"

echo "=== All Repositories Updated Successfully ==="

# Run the build-website.py script
echo "=== Building Website ==="
python "$ROOT_DIR/scripts/build_website.py" --skip-tests
echo "=== Website Built Successfully ==="

## Run the push_changes.sh script
#echo "=== Pushing Changes ==="
#bash "$ROOT_DIR/scripts/push_changes.sh"
#echo "=== Process Completed Successfully ==="