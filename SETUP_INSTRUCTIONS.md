# Setup Instructions for RedPen Project

This document provides instructions for setting up the RedPen project with its Git submodules structure.

## Repository Structure

The RedPen project is split into three repositories:

1. **redpen-infra**: Main repository containing the infrastructure code and scripts
2. **redpen-content**: Repository containing the content files (images, text, annotations)
3. **redpen-publish**: Repository for the published static website

## Setup Steps

### 1. Create Remote Repositories

First, create the three repositories on GitHub or your preferred Git hosting service:

- `redpen-infra`
- `redpen-content`
- `redpen-publish`

### 2. Initialize Local Repositories

#### Main Repository (redpen-infra)

```bash
# Clone the main repository
git clone git@github.com:volokhonsky/redpen-infra.git
cd redpen-infra

# Add and commit the initial files
git add .
git commit -m "Initial commit for redpen-infra"
git push origin main
```

#### Content Repository (redpen-content)

```bash
# Initialize the content repository
cd redpen-content
git init
git add .
git commit -m "Initial commit for redpen-content"

# Add remote and push
git remote add origin git@github.com:volokhonsky/redpen-content.git
git push -u origin main
```

#### Publish Repository (redpen-publish)

```bash
# Initialize the publish repository
cd redpen-publish
git init
git add .
git commit -m "Initial commit for redpen-publish"

# Add remote and push
git remote add origin git@github.com:volokhonsky/redpen-publish.git
git push -u origin main
```

### 3. Set Up Submodules

From the main repository:

```bash
# Remove the existing directories
rm -rf redpen-content redpen-publish

# Add the repositories as submodules
git submodule add git@github.com:volokhonsky/redpen-content.git redpen-content
git submodule add git@github.com:volokhonsky/redpen-publish.git redpen-publish

# Commit the changes
git commit -am "Add redpen-content and redpen-publish as submodules"
git push origin main
```

### 4. Cloning the Project with Submodules

To clone the project with all submodules:

```bash
git clone --recurse-submodules git@github.com:volokhonsky/redpen-infra.git
cd redpen-infra
```

Or if you've already cloned the repository without submodules:

```bash
git submodule init
git submodule update
```

## Working with Submodules

### Updating Submodules

To update all submodules to their latest commits:

```bash
git submodule update --remote
```

### Making Changes to Submodules

```bash
# Navigate to the submodule
cd redpen-content

# Make changes, commit, and push
git add .
git commit -m "Update content"
git push origin main

# Go back to the main repository and update the submodule reference
cd ..
git add redpen-content
git commit -m "Update redpen-content submodule"
git push origin main
```

## Deployment

For deployment, you'll need to ensure that the submodules are properly cloned and updated on your deployment server. Most CI/CD systems have options for handling submodules.
