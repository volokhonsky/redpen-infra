# Annotation Positioning Tests

This directory contains automated tests to verify the positioning of annotation circles at different screen widths and during window resizing.

## Overview

The tests focus on page 7 of the RedPen application and verify that annotation circles are positioned correctly at different screen widths and that they reposition correctly when the window is resized.

The tests measure the positions of circles at:
- Desktop width (1280px)
- Mobile width (800px)
- After resizing from desktop to mobile
- After resizing from mobile to desktop

The measured positions are compared against baseline values to ensure that the positioning remains correct as the codebase evolves.

## Running the Tests

To run the tests, use the provided shell script:

```bash
./tests/run_annotation_tests.sh
```

This will:
1. Install the required dependencies
2. Install Playwright browsers if needed
3. Run the tests against the existing baseline positions
4. Generate screenshots in the `tests/results` directory

## Test Cases

The test suite includes the following test cases:

### Test 1: Desktop Width (1280px)
- Loads page 7 in a browser with a 1280x800 viewport
- Measures the positions of all annotation circles
- Compares the positions against the baseline values
- Generates a screenshot in `tests/results/desktop_width.png`

### Test 2: Mobile Width (800px)
- Loads page 7 in a browser with an 800x600 viewport
- Measures the positions of all annotation circles
- Compares the positions against the baseline values
- Generates a screenshot in `tests/results/mobile_width.png`

### Test 3: Resize from Desktop to Mobile
- Loads page 7 in a browser with a 1280x800 viewport
- Takes a screenshot before resizing
- Resizes the viewport to 800x600
- Measures the positions of all annotation circles after resizing
- Compares the positions against the baseline values
- Generates screenshots in:
  - `tests/results/desktop_before_resize.png`
  - `tests/results/desktop_to_mobile_after_resize.png`

### Test 4: Resize from Mobile to Desktop
- Loads page 7 in a browser with an 800x600 viewport
- Takes a screenshot before resizing
- Resizes the viewport to 1280x800
- Measures the positions of all annotation circles after resizing
- Compares the positions against the baseline values
- Generates screenshots in:
  - `tests/results/mobile_before_resize.png`
  - `tests/results/mobile_to_desktop_after_resize.png`

## Baseline Positions

The baseline positions are stored in `tests/baseline_positions.json` and represent the expected positions of circles at different screen widths.

To update the baseline positions, run:

```bash
./tests/run_annotation_tests.sh --update-baseline
```

This will run the tests and update the baseline positions with the current measured values. Use this when:
- You've made intentional changes to the positioning logic
- You've added or removed annotations
- You've changed the layout of the application

## Interpreting Test Results

The tests will output:
- The number of circles found at each screen width
- The positions of each circle (centerX, centerY)
- Whether the positions match the baseline values
- An overall PASS/FAIL result

If a test fails, check:
1. The screenshots in the `tests/results` directory to see the actual positions
2. The console output to see which positions don't match the baseline
3. Whether you need to update the baseline positions due to intentional changes

## Adding More Tests

To add more test cases:
1. Modify `tests/annotation_position_tests.py` to add new test functions
2. Update the `run_tests` function to call your new test functions
3. Update the baseline positions by running with `--update-baseline`
4. Update this documentation to describe the new test cases
