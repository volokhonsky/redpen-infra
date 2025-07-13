/**
 * Annotation handling for RedPen application
 */

/**
 * Function to reposition annotations based on current image size
 */
function repositionAnnotations() {
  if (!overlayContainer || !currentPageId || allAnns.length === 0) return;

  // Counter for sequential annotation IDs within the page
  let annotationCounter = 0;

  const img = document.getElementById('page-image');
  if (!img.complete) return;

  // Calculate scale factors
  const originalWidth = img.naturalWidth;
  const originalHeight = img.naturalHeight;
  const scaleX = img.width / originalWidth;
  const scaleY = img.height / originalHeight;

  // Update overlay container size and position
  overlayContainer.style.width = img.width + 'px';
  overlayContainer.style.height = img.height + 'px';
  overlayContainer.style.top = img.offsetTop + 'px';
  overlayContainer.style.left = img.offsetLeft + 'px';

  // Log overlay container position for debugging
  console.log('Overlay container updated in repositionAnnotations:', {
    top: overlayContainer.style.top,
    left: overlayContainer.style.left,
    width: overlayContainer.style.width,
    height: overlayContainer.style.height,
    offsetTop: img.offsetTop,
    offsetLeft: img.offsetLeft,
    imgWidth: img.width,
    imgHeight: img.height,
    imgNaturalWidth: img.naturalWidth,
    imgNaturalHeight: img.naturalHeight,
    scaleX: scaleX,
    scaleY: scaleY,
    computedStyle: {
      position: window.getComputedStyle(overlayContainer).position,
      top: window.getComputedStyle(overlayContainer).top,
      left: window.getComputedStyle(overlayContainer).left
    }
  });

  // Log window size for debugging
  console.log('Window size:', {
    width: window.innerWidth,
    height: window.innerHeight
  });

  // Remove existing circles and popups
  document.querySelectorAll('.circle').forEach(circle => circle.remove());
  document.querySelectorAll('.comment-popup').forEach(popup => popup.remove());

  // Recreate circles and popups with updated positions
  const sizeMap = { main: 100, comment: 50, small: 25 };

  // Load text blocks
  let textBlocks = {};
  console.log(`Fetching text blocks from: text/${currentPageId}.json`);
  fetch('text/' + currentPageId + '.json')
    .then(r => {
      if (!r.ok) {
        throw new Error(`Failed to fetch text blocks: ${r.status} ${r.statusText}`);
      }
      return r.json();
    })
    .then(tl => {
      console.log('Text blocks loaded:', tl);
      tl.forEach(b => {
        const [x0, y0, x1, y1] = b.bbox.map(v => v * 2);
        textBlocks[b.id] = [x0, y0, x1, y1];
      });
      console.log('Processed text blocks:', textBlocks);
      console.log('Annotations to process:', allAnns);

      // Filter out general comments for separate processing
      const generalComments = allAnns.filter(a => a.annType === 'general');
      const nonGeneralComments = allAnns.filter(a => a.annType !== 'general');

      // Create circles and popups for non-general comments
      nonGeneralComments.forEach((a, i) => {
        // Increment counter for each annotation
        annotationCounter++;
        let cx, cy;
        const bb = textBlocks[a.targetBlock];
        console.log(`Processing annotation ${i+1} (ID: ${a.id || 'no-id'}):`);
        console.log(`  Target block: ${a.targetBlock}, Found: ${bb ? 'Yes' : 'No'}`);
        console.log(`  Fallback coords: ${a.coords ? a.coords.join(',') : 'None'}`);

        if (bb) {
          // Apply scale factor to the bounding box coordinates
          const scaledBB = [
            bb[0] * scaleX,
            bb[1] * scaleY,
            bb[2] * scaleX,
            bb[3] * scaleY
          ];
          [cx, cy] = [scaledBB[2], (scaledBB[1] + scaledBB[3]) / 2];
          console.log(`  Using text block. Scaled BB: [${scaledBB.join(', ')}]`);
          console.log(`  Calculated position: (${cx}, ${cy})`);
        } else if (a.coords) {
          // Apply scale factor to the fallback coordinates
          [cx, cy] = [a.coords[0] * scaleX, a.coords[1] * scaleY];
          console.log(`  Using fallback coords. Scaled: (${cx}, ${cy})`);
        } else {
          console.log(`  No position found, skipping annotation`);
          return;
        }

        const d = sizeMap[a.annType] || 50;
        const circle = document.createElement('div');
        circle.className = 'circle';
        // Add unique ID to circle based on annotation ID or generate one using sequential counter
        circle.id = 'circle-' + (a.id || `${currentPageId}-${annotationCounter}`);
        circle.style.width = d + 'px';
        circle.style.height = d + 'px';
        circle.style.left = cx + 'px';  // Now using translateX in CSS for centering
        circle.style.top = cy - d / 2 + 'px';
        const color = a.annType === 'main' ? '#DC143C' : '#0000FF';
        circle.style.background = `radial-gradient(circle, ${color}80 0%, ${color}40 50%, ${color}00 100%)`;
        circle.style.fontSize = (d * 0.6) + 'px';
        // Ensure transform property is not overridden
        circle.style.transform = 'translateX(-50%)';
        circle.textContent = i + 1;

        // Use only this comment's text
        let commentText = a.text;

        // Create popup for desktop hover using the comment-content.js function
        const popup = createCommentPopup(a, i, cx, cy, d);
        overlayContainer.appendChild(popup);
        console.log(`  Popup created with ID: ${popup.id}`);

        // Desktop: Show popup on hover
        if (!isMobile) {
          circle.addEventListener('mouseenter', () => {
            // Only show on hover if not already shown by click
            if (popup.dataset.clickShown !== 'true') {
              popup.style.display = 'block';
              popup.dataset.hoverShown = 'true';
            }
          });

          circle.addEventListener('mouseleave', () => {
            // Only hide on mouse leave if it was shown by hover, not by click
            if (popup.dataset.hoverShown === 'true' && popup.dataset.clickShown !== 'true') {
              // Use setTimeout to allow time for mouseenter on popup
              setTimeout(() => {
                if (popup.dataset.popupHover !== 'true') {
                  popup.style.display = 'none';
                  popup.dataset.hoverShown = 'false';
                }
              }, 50);
            }
          });

          // Add mouseenter/mouseleave events to popup itself
          popup.addEventListener('mouseenter', () => {
            popup.dataset.popupHover = 'true';
          });

          popup.addEventListener('mouseleave', () => {
            popup.dataset.popupHover = 'false';
            // Only hide if it was shown by hover, not by click
            if (popup.dataset.hoverShown === 'true' && popup.dataset.clickShown !== 'true') {
              popup.style.display = 'none';
              popup.dataset.hoverShown = 'false';
            }
          });
        }

        // Handle click events
        circle.addEventListener('click', (e) => {
          if (isMobile) {
            // Mobile: Show overlay
            // Use the combined text that includes general comments
            showMobileComment(i, commentText);
          } else {
            // Desktop: Update sidebar list and show popup using the comment-content.js function
            updateCommentSidebar(i, commentText);

            // Reset all popups' click-shown state
            document.querySelectorAll('.comment-popup').forEach(p => {
              p.dataset.clickShown = 'false';
              if (p !== popup) p.style.display = 'none';
            });

            // Show this popup and mark it as shown by click
            popup.style.display = 'block';
            popup.dataset.clickShown = 'true';

            // Stop propagation to prevent the document click handler from immediately hiding it
            e.stopPropagation();
          }
        });

        overlayContainer.appendChild(circle);
        console.log(`  Circle created with ID: ${circle.id}`);
      });

      // Add document-level click event to hide all popups when clicking elsewhere
      if (!isMobile) {
        // Remove any existing document click listener first to avoid duplicates
        document.removeEventListener('click', hideAllPopups);

        // Add new document click listener
        document.addEventListener('click', hideAllPopups);
      }
    })
    .catch(e => {
      console.error('Error repositioning annotations:', e);
      console.error('Stack trace:', e.stack);
      // Try to create annotations using fallback coordinates even if text blocks failed to load
      if (allAnns && allAnns.length > 0) {
        console.log('Attempting to create annotations using only fallback coordinates...');

        // Filter out general comments for separate processing
        const generalComments = allAnns.filter(a => a.annType === 'general');
        const nonGeneralComments = allAnns.filter(a => a.annType !== 'general');

        nonGeneralComments.forEach((a, i) => {
          if (a.coords) {
            // Increment counter for each annotation in fallback section
            annotationCounter++;
            const cx = a.coords[0] * scaleX;
            const cy = a.coords[1] * scaleY;
            console.log(`Creating annotation ${i+1} at fallback position (${cx}, ${cy})`);

            const d = sizeMap[a.annType] || 50;
            const circle = document.createElement('div');
            circle.className = 'circle';
            circle.id = 'circle-' + (a.id || `${currentPageId}-${annotationCounter}`);
            circle.style.width = d + 'px';
            circle.style.height = d + 'px';
            circle.style.left = cx + 'px';
            circle.style.top = cy - d / 2 + 'px';
            const color = a.annType === 'main' ? '#DC143C' : '#0000FF';
            circle.style.background = `radial-gradient(circle, ${color}80 0%, ${color}40 50%, ${color}00 100%)`;
            circle.style.fontSize = (d * 0.6) + 'px';
            circle.textContent = i + 1;

            // Use only this comment's text
            let commentText = a.text;

            const popup = document.createElement('div');
            popup.className = 'comment-popup';
            popup.id = (a.id || `ann-${currentPageId}-${annotationCounter}`);
            popup.innerHTML = `
              <div class="comment-popup-title">Комментарий ${i + 1}</div>
              <div>${commentText}</div>
            `;
            popup.style.left = cx + 'px';
            popup.style.top = (cy + d / 2 + 10) + 'px';
            popup.style.visibility = 'hidden'; // Hide initially to measure
            popup.style.display = 'block';
            popup.dataset.hoverShown = 'false';
            popup.dataset.clickShown = 'false';

            // Append to DOM temporarily to get dimensions
            document.body.appendChild(popup);

            // Check if popup extends beyond viewport bottom
            const popupRect = popup.getBoundingClientRect();
            const viewportHeight = window.innerHeight;

            // If popup extends beyond viewport bottom, position it above the annotation
            if (popupRect.bottom > viewportHeight) {
              // Position above the annotation circle instead of below
              popup.style.top = (cy - d / 2 - popup.offsetHeight - 10) + 'px';
              // Add class to flip the arrow direction
              popup.classList.add('above');
            } else {
              // Keep original position below the annotation
              popup.style.top = (cy + d / 2 + 10) + 'px';
              // Remove the above class if it exists
              popup.classList.remove('above');
            }

            // Remove from body - it will be added to the proper container later
            document.body.removeChild(popup);
            popup.style.visibility = 'visible';
            popup.style.display = 'none'; // Reset display to none (will be shown on hover/click)

            overlayContainer.appendChild(popup);
            overlayContainer.appendChild(circle);

            // Desktop: Show popup on hover
            if (!isMobile) {
              circle.addEventListener('mouseenter', () => {
                // Only show on hover if not already shown by click
                if (popup.dataset.clickShown !== 'true') {
                  popup.style.display = 'block';
                  popup.dataset.hoverShown = 'true';
                }
              });

              circle.addEventListener('mouseleave', () => {
                // Only hide on mouse leave if it was shown by hover, not by click
                if (popup.dataset.hoverShown === 'true' && popup.dataset.clickShown !== 'true') {
                  // Use setTimeout to allow time for mouseenter on popup
                  setTimeout(() => {
                    if (popup.dataset.popupHover !== 'true') {
                      popup.style.display = 'none';
                      popup.dataset.hoverShown = 'false';
                    }
                  }, 50);
                }
              });

              // Add mouseenter/mouseleave events to popup itself
              popup.addEventListener('mouseenter', () => {
                popup.dataset.popupHover = 'true';
              });

              popup.addEventListener('mouseleave', () => {
                popup.dataset.popupHover = 'false';
                // Only hide if it was shown by hover, not by click
                if (popup.dataset.hoverShown === 'true' && popup.dataset.clickShown !== 'true') {
                  popup.style.display = 'none';
                  popup.dataset.hoverShown = 'false';
                }
              });
            }

            // Add click event handler for mobile
            circle.addEventListener('click', (e) => {
              if (isMobile) {
                // Mobile: Show overlay with combined text
                showMobileComment(i, commentText);
              } else {
                // Desktop: Update sidebar list and show popup
                const listUl = document.getElementById('comment-list');
                listUl.innerHTML = '';
                const li = document.createElement('li');
                li.innerHTML = '<strong>' + (i + 1) + '.</strong> ' + commentText;
                listUl.appendChild(li);

                // Reset all popups' click-shown state
                document.querySelectorAll('.comment-popup').forEach(p => {
                  p.dataset.clickShown = 'false';
                  if (p !== popup) p.style.display = 'none';
                });

                // Show this popup and mark it as shown by click
                popup.style.display = 'block';
                popup.dataset.clickShown = 'true';

                // Stop propagation to prevent the document click handler from immediately hiding it
                e.stopPropagation();
              }
            });

            console.log(`Created fallback circle with ID: ${circle.id}`);
            console.log(`Created fallback popup with ID: ${popup.id}`);
          }
        });
      }
    });
}

/**
 * Function to hide all comment popups
 */
function hideAllPopups() {
  document.querySelectorAll('.comment-popup').forEach(popup => {
    popup.style.display = 'none';
    popup.dataset.clickShown = 'false';
  });
}
