/**
 * Annotation handling for RedPen application
 */

/**
 * Function to reposition annotations based on current image size
 */
function repositionAnnotations() {
  if (!overlayContainer || !currentPageId || allAnns.length === 0) return;

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

      // Create circles and popups
      allAnns.forEach((a, i) => {
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
        // Add unique ID to circle based on annotation ID or generate one
        circle.id = 'circle-' + (a.id || `${currentPageId}-${i+1}`);
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

        // Create popup for desktop hover
        const popup = document.createElement('div');
        popup.className = 'comment-popup';
        // Add unique ID to popup based on annotation ID or generate one
        popup.id = (a.id || `ann-${currentPageId}-${i+1}`);
        popup.innerHTML = `
          <div class="comment-popup-title">Комментарий ${i + 1}</div>
          <div>${a.text}</div>
        `;
        // Center the popup under the circle
        popup.style.left = cx + 'px';  // Now using translateX in CSS for centering
        popup.style.top = (cy + d / 2 + 10) + 'px';
        // Ensure transform property is not overridden
        popup.style.transform = 'translateX(-50%)';
        // Initialize dataset attributes for tracking hover and click states
        popup.dataset.hoverShown = 'false';
        popup.dataset.clickShown = 'false';
        overlayContainer.appendChild(popup);
        console.log(`  Popup created with ID: ${popup.id}`);

        // Prevent popup from closing when clicking on it
        popup.addEventListener('click', (e) => {
          e.stopPropagation();
        });

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
              popup.style.display = 'none';
              popup.dataset.hoverShown = 'false';
            }
          });
        }

        // Handle click events
        circle.addEventListener('click', (e) => {
          if (isMobile) {
            // Mobile: Show overlay
            showMobileComment(i, a.text);
          } else {
            // Desktop: Update sidebar list and show popup
            const listUl = document.getElementById('comment-list');
            listUl.innerHTML = '';
            const li = document.createElement('li');
            li.innerHTML = '<strong>' + (i + 1) + '.</strong> ' + a.text;
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
        allAnns.forEach((a, i) => {
          if (a.coords) {
            const cx = a.coords[0] * scaleX;
            const cy = a.coords[1] * scaleY;
            console.log(`Creating annotation ${i+1} at fallback position (${cx}, ${cy})`);

            const d = sizeMap[a.annType] || 50;
            const circle = document.createElement('div');
            circle.className = 'circle';
            circle.id = 'circle-' + (a.id || `${currentPageId}-${i+1}`);
            circle.style.width = d + 'px';
            circle.style.height = d + 'px';
            circle.style.left = cx + 'px';
            circle.style.top = cy - d / 2 + 'px';
            const color = a.annType === 'main' ? '#DC143C' : '#0000FF';
            circle.style.background = `radial-gradient(circle, ${color}80 0%, ${color}40 50%, ${color}00 100%)`;
            circle.style.fontSize = (d * 0.6) + 'px';
            circle.textContent = i + 1;

            const popup = document.createElement('div');
            popup.className = 'comment-popup';
            popup.id = (a.id || `ann-${currentPageId}-${i+1}`);
            popup.innerHTML = `
              <div class="comment-popup-title">Комментарий ${i + 1}</div>
              <div>${a.text}</div>
            `;
            popup.style.left = cx + 'px';
            popup.style.top = (cy + d / 2 + 10) + 'px';
            popup.dataset.hoverShown = 'false';
            popup.dataset.clickShown = 'false';

            overlayContainer.appendChild(popup);
            overlayContainer.appendChild(circle);

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
