#!/bin/bash

# Run annotation positioning tests for RedPen

# Change to the project root directory
cd "$(dirname "$0")/.."

echo "=== RedPen Annotation Positioning Tests ==="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

# Check if pip is installed
if ! command -v pip &> /dev/null; then
    echo "Error: pip is required but not installed."
    exit 1
fi

# Install dependencies if needed
echo "Checking dependencies..."
pip install -r tests/requirements.txt

echo "Installing Playwright browsers if needed..."
python3 -m playwright install chromium

# Parse command line arguments
UPDATE_BASELINE=false
for arg in "$@"
do
    case $arg in
        --update-baseline)
        UPDATE_BASELINE=true
        shift
        ;;
        *)
        # Unknown option
        ;;
    esac
done

# Run the tests
if [ "$UPDATE_BASELINE" = true ]; then
    echo "Running tests and updating baseline positions..."
    python3 tests/annotation_position_tests.py --update-baseline
else
    echo "Running tests against existing baseline positions..."
    python3 tests/annotation_position_tests.py
fi

echo ""
echo "Tests completed!"
echo "Check the results directory for screenshots:"
echo "- tests/results/"

if [ "$UPDATE_BASELINE" = true ]; then
    echo "Baseline positions have been updated in:"
    echo "- tests/baseline_positions.json"
fi