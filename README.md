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
- `tests/`: Automated tests for the application
  - `annotation_position_tests.py`: Tests for annotation positioning at different screen widths
  - `run_annotation_tests.sh`: Shell script to run annotation tests
  - `requirements.txt`: Dependencies for testing tools
  - `baseline_positions.json`: Baseline positions for annotation tests
  - `README.md`: Documentation for annotation tests
- Submodules:
  - `redpen-content/`: Repository containing content files (images, text, annotations)
  - `redpen-publish/`: Repository for the published static website

## Features

- Extract images and text from PDF files
- Generate annotation templates
- Display textbook pages with annotations
- Responsive design for both desktop and mobile viewing
- Diagnostic tools for troubleshooting element positioning

## Setup

1. Clone this repository with submodules:
   ```bash
   git clone --recurse-submodules git@github.com:volokhonsky/redpen-infra.git
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

### Running Annotation Tests

To verify the positioning of annotation circles at different screen widths:

```bash
./tests/run_annotation_tests.sh
```

This will run the annotation positioning tests, which measure the positions of circles at desktop width (1280px), mobile width (800px), and during window resizing. See `tests/README.md` for more details.

To update the baseline positions for the tests:

```bash
./tests/run_annotation_tests.sh --update-baseline
```

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
