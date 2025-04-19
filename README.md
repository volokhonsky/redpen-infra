# RedPen Infrastructure Repository

This is the main repository for the RedPen project, which processes PDF textbooks and displays them with annotations.

## Repository Structure

- `scripts/`: Python scripts for processing PDF files
  - `extract_images.py`: Extracts images from PDF files
  - `extract_text.py`: Extracts text from PDF files
  - `generate_annotations.py`: Generates annotation templates
  - `process_pdf.py`: Main script that orchestrates the entire workflow
  - `publish_data.py`: Publishes data to the content repository
  - `requirements.txt`: Python dependencies
- `templates/`: Templates for the static website
  - `css/`: Stylesheets for the website
  - `js/`: JavaScript files for functionality
  - `index.html`: Main HTML file for the static website
  - `favicon.svg`: Favicon for the website
- Submodules:
  - `redpen-content/`: Repository containing content files (images, text, annotations)
  - `redpen-publish/`: Repository for the published static website

## Features

- Extract images and text from PDF files
- Generate annotation templates
- Display textbook pages with annotations
- Responsive design for both desktop and mobile viewing

## Setup

1. Clone this repository with submodules:
   ```bash
   git clone --recurse-submodules git@github.com:you/redpen-infra.git
   cd redpen-infra
   ```

2. Install Python dependencies:
   ```bash
   pip install -r scripts/requirements.txt
   ```

## Usage

### Processing a PDF

```bash
python scripts/process_pdf.py path/to/textbook.pdf
```

### Viewing the Website

Open `templates/index.html` in a web browser to view the processed content, or use the fully functional website in the `redpen-publish` directory by opening `redpen-publish/index.html`.

## Workflow

1. PDF files are processed using the scripts in this repository
2. Generated content is stored in the RedPen content repository
3. The static website in the RedPen publish repository displays the content

## Project Structure

The RedPen project is split into three repositories:

1. **redpen-infra** (this repository): Main repository containing the infrastructure code and scripts
2. **redpen-content**: Repository containing the content files (images, text, annotations)
3. **redpen-publish**: Repository for the published static website

These repositories are connected using Git submodules, with this repository as the main repository and the others as submodules.
