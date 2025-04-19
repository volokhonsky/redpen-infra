# RedPen Project Structure

This document explains the structure of the RedPen project, which is split into three separate repositories.

## Overview

The RedPen project consists of three repositories:

1. **Frontend Repository** - Contains the static website that displays annotated textbook pages
2. **PDF Processing Repository** - Contains Python scripts for processing PDF files and generating data
3. **Artifacts Repository** - Stores the generated data (images and JSON files)

## Repository Details

### 1. Frontend Repository

**Purpose**: Serves as a static website that displays annotated textbook pages.

**Key Components**:
- `templates/` - Contains templates for the static website
  - `index.html` - Main HTML file
  - `css/` - Stylesheets for the website
  - `js/` - JavaScript files for functionality
  - `favicon.svg` - Favicon for the website
- References data from the Artifacts Repository

### 2. PDF Processing Repository

**Purpose**: Processes PDF files to extract images, text, and generate annotation templates.

**Key Components**:
- `process_pdf.py` - Main script that orchestrates the entire workflow
- `extract_images.py` - Extracts images from PDF files
- `extract_text.py` - Extracts text from PDF files and converts to JSON
- `generate_annotations.py` - Generates annotation templates
- `publish_data.py` - Publishes data to the Artifacts Repository

### 3. Artifacts Repository

**Purpose**: Stores the generated data and contains a fully functional website that can be deployed directly.

**Key Components**:
- `images/` - Contains PNG images of textbook pages
- `text/` - Contains JSON files with text data
- `annotations/` - Contains JSON files with annotation data
- `templates/` - Contains templates for the static website
  - `css/` - Stylesheets for the website
  - `js/` - JavaScript files for functionality
  - `favicon.svg` - Favicon for the website
- `index.html` - Main HTML file for the static website

## Workflow

1. **PDF Processing**:
   - A PDF file is processed using the scripts in the PDF Processing Repository
   - Images are extracted from the PDF
   - Text is extracted and converted to structured JSON
   - Annotation templates are generated

2. **Data Publishing**:
   - The generated data (images, text, annotations) is published to the Artifacts Repository

3. **Frontend Display**:
   - The Frontend Repository uses the data from the Artifacts Repository to display the content
   - Users can view the textbook pages with annotations in a web browser

## Development Setup

To set up the project for development:

1. Clone all three repositories
2. Install dependencies for the PDF Processing Repository:
   ```bash
   cd redpen-infra
   pip install -r scripts/requirements.txt
   ```
3. Process a PDF file:
   ```bash
   python scripts/process_pdf.py path/to/textbook.pdf --artifacts-repo ../redpen-publish
   ```
4. Open the Frontend Repository's templates/index.html in a web browser to view the results, or use the fully functional website in the Artifacts Repository by opening its index.html

## Deployment

For deployment:

1. Process PDF files using the PDF Processing Repository
2. Deploy the Artifacts Repository to a web-accessible location
3. Deploy the Frontend Repository as a static website
4. Configure the Frontend Repository to access the Artifacts Repository at its deployed location
