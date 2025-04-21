#!/bin/bash

# Script to add, commit, and push changes to all repositories
# This script handles the main repository and both submodules (redpen-content and redpen-publish)

# Exit on error
set -e

# Get the root directory of the project
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Pushing Changes to All Repositories ==="

# Function to add, commit, and push changes to a repository
push_repo() {
    local repo_path="$1"
    local repo_name="$2"
    
    echo "Processing repository: $repo_name"
    
    # Change to the repository directory
    cd "$repo_path"
    
    # Check if there are changes to commit
    if [ -z "$(git status --porcelain)" ]; then
        echo "No changes to commit in $repo_name"
    else
        # Add all changes
        echo "Adding changes in $repo_name..."
        git add .
        
        # Commit changes
        echo "Committing changes in $repo_name..."
        git commit -m "Update content via push_changes script"
        
        # Push changes
        echo "Pushing changes in $repo_name..."
        git push
        
        echo "Successfully pushed changes to $repo_name"
    fi
    
    # Return to the root directory
    cd "$ROOT_DIR"
}

# Add all new pages to git
echo "Adding new pages to git..."
git add -A

# Push changes to redpen-content submodule
push_repo "$ROOT_DIR/redpen-content" "redpen-content"

# Push changes to redpen-publish submodule
push_repo "$ROOT_DIR/redpen-publish" "redpen-publish"

# Push changes to main repository
push_repo "$ROOT_DIR" "main repository"

echo "=== All Changes Pushed Successfully ==="