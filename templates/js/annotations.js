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

  // Update overlay container position and size
  overlayContainer.style.top = img.offsetTop + 'px';
  overlayContainer.style.left = img.offsetLeft + 'px';
  overlayContainer.style.width = img.width + 'px';
  overlayContainer.style.height = img.height + 'px';

  // Remove existing circles and popups
  document.querySelectorAll('.circle').forEach(circle => circle.remove());
  document.querySelectorAll('.comment-popup').forEach(popup => popup.remove());

  // Recreate circles and popups with updated positions
  const sizeMap = { main: 100, comment: 50, small: 25 };

  // Load text blocks
  let textBlocks = {};
  fetch('redpen-content/text/' + currentPageId + '.json')
    .then(r => r.json())
    .then(tl => {
      tl.forEach(b => {
        const [x0, y0, x1, y1] = b.bbox.map(v => v * 2);
        textBlocks[b.id] = [x0, y0, x1, y1];
      });

      // Create circles and popups
      allAnns.forEach((a, i) => {
        let cx, cy;
        const bb = textBlocks[a.targetBlock];
        if (bb) {
          // Apply scale factor to the bounding box coordinates
          const scaledBB = [
            bb[0] * scaleX,
            bb[1] * scaleY,
            bb[2] * scaleX,
            bb[3] * scaleY
          ];
          [cx, cy] = [(scaledBB[0] + scaledBB[2]) / 2, (scaledBB[1] + scaledBB[3]) / 2];
        } else if (a.coords) {
          // Apply scale factor to the fallback coordinates
          [cx, cy] = [a.coords[0] * scaleX, a.coords[1] * scaleY];
        } else return;

        const d = sizeMap[a.annType] || 50;
        const circle = document.createElement('div');
        circle.className = 'circle';
        circle.style.width = d + 'px';
        circle.style.height = d + 'px';
        circle.style.left = cx - d / 2 + 'px';
        circle.style.top = cy - d / 2 + 'px';
        const color = a.annType === 'main' ? '#DC143C' : '#0000FF';
        circle.style.background = `radial-gradient(circle, ${color}80 0%, ${color}40 50%, ${color}00 100%)`;
        circle.style.fontSize = (d * 0.6) + 'px';
        circle.textContent = i + 1;

        // Create popup for desktop hover
        const popup = document.createElement('div');
        popup.className = 'comment-popup';
        popup.innerHTML = `
          <div class="comment-popup-title">Комментарий ${i + 1}</div>
          <div>${a.text}</div>
        `;
        popup.style.left = cx + 'px';
        popup.style.top = (cy + d / 2 + 10) + 'px';
        // Initialize dataset attributes for tracking hover and click states
        popup.dataset.hoverShown = 'false';
        popup.dataset.clickShown = 'false';
        document.getElementById('image-container').appendChild(popup);

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
      });

      // Add document-level click event to hide all popups when clicking elsewhere
      if (!isMobile) {
        // Remove any existing document click listener first to avoid duplicates
        document.removeEventListener('click', hideAllPopups);

        // Add new document click listener
        document.addEventListener('click', hideAllPopups);
      }
    })
    .catch(e => console.warn('Error repositioning annotations:', e));
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
