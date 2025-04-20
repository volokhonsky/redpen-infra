# Annotation Positioning Fix for Window Resize

## Issue Description

When the window size was changed after annotations were displayed, the annotation circles remained in their original position relative to the left edge of the screen, while the image moved. This caused the annotations to become misaligned with the image content.

## Root Cause

The issue was in the `repositionAnnotations` function in `annotations.js`. When the window was resized, the function updated the size of the overlay container (width and height) but not its position (top and left). This meant that when the image moved due to window resizing, the overlay container didn't move with it, causing the annotations to become misaligned.

## Solution

The solution was to update both the size and position of the overlay container in the `repositionAnnotations` function. Now, when the window is resized, the overlay container's position is updated to match the image's current position, ensuring that the annotations remain correctly aligned with the image content.

### Changes Made

1. Modified the `repositionAnnotations` function in `annotations.js` to update the overlay container's position:

```javascript
// Before:
// Update overlay container size only, not position
// Don't reset the left position that was set in main.js
overlayContainer.style.width = img.width + 'px';
overlayContainer.style.height = img.height + 'px';

// After:
// Update overlay container size and position
overlayContainer.style.width = img.width + 'px';
overlayContainer.style.height = img.height + 'px';
overlayContainer.style.top = img.offsetTop + 'px';
overlayContainer.style.left = img.offsetLeft + 'px';
```

2. Added additional logging to help with debugging and verification:

```javascript
// Log window size for debugging
console.log('Window size:', {
  width: window.innerWidth,
  height: window.innerHeight
});
```

## Testing

The solution was tested by:

1. Loading a page with annotations
2. Resizing the window to various sizes
3. Verifying that the annotations remained correctly aligned with the image content

The fix ensures that annotations reposition correctly with the image when the window is resized, providing a better user experience.