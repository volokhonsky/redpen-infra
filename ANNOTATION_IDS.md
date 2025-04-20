# Annotation Element IDs

This document explains the ID system used for annotation elements in the RedPen application.

## Overview

To simplify testing and debugging, unique IDs have been added to all annotation elements (circles and popups). These IDs are based on the annotation's own ID or generated from the page ID and index if no annotation ID is available.

## ID Formats

### Circle Elements

Circles (the visual markers for annotations) use the following ID format:
```
circle-{annotation_id}
```

Or if no annotation ID is available:
```
circle-{page_id}-{index}
```

Example: `circle-ann-page7-1` or `circle-page_007-1`

### Popup Elements

Popups (the comment boxes that appear when hovering/clicking on circles) use the following ID format:
```
{annotation_id}
```

Or if no annotation ID is available:
```
ann-{page_id}-{index}
```

Example: `ann-page7-1` or `ann-page_007-1`

## Usage in Testing

These IDs can be used in testing scripts to easily locate and interact with specific annotation elements:

```javascript
// Example using Playwright
await page.click('#circle-ann-page7-1');
await expect(page.locator('#ann-page7-1')).toBeVisible();
```

```python
# Example using Playwright in Python
page.click('#circle-ann-page7-1')
expect(page.locator('#ann-page7-1')).to_be_visible()
```

## Implementation

The IDs are set in the `repositionAnnotations` function in `annotations.js`:

```javascript
// For circles
circle.id = 'circle-' + (a.id || `${currentPageId}-${i+1}`);

// For popups
popup.id = (a.id || `ann-${currentPageId}-${i+1}`);
```