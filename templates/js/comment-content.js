/**
 * Comment content handling for RedPen application
 * This file contains functions specifically for rendering and formatting the content inside annotations
 */

/**
 * Renders the content of a comment popup
 * @param {number} index - The index of the annotation
 * @param {string} text - The text content of the annotation
 * @returns {string} HTML content for the comment popup
 */
function renderCommentContent(index, text) {
  return `
    <div class="comment-popup-title">Комментарий ${index + 1}</div>
    <div class="comment-content">${formatCommentText(text)}</div>
  `;
}

/**
 * Formats the text content of a comment
 * @param {string} text - The raw text content
 * @returns {string} Formatted HTML content
 */
function formatCommentText(text) {
  // Return empty string for empty text
  if (!text) return '';

  // Check if marked library is available
  if (typeof marked !== 'undefined') {
    // Configure marked options
    marked.setOptions({
      gfm: true,      // GitHub flavored markdown
      breaks: true,   // Convert line breaks to <br>
      sanitize: false // Allow HTML in the source
    });

    // Use marked to parse Markdown
    try {
      return marked.parse(text);
    } catch (e) {
      console.error('Error parsing markdown:', e);
      // Fall back to basic formatting if marked fails
    }
  }

  // Basic formatting fallback if marked is not available or fails
  // Convert line breaks to <br> tags
  let formattedText = text.replace(/\n/g, '<br>');

  // Highlight important parts (text between asterisks)
  formattedText = formattedText.replace(/\*([^*]+)\*/g, '<strong>$1</strong>');

  return formattedText;
}

/**
 * Creates a comment popup element
 * @param {Object} annotation - The annotation object
 * @param {number} index - The index of the annotation
 * @param {number} cx - X coordinate for positioning
 * @param {number} cy - Y coordinate for positioning
 * @param {number} diameter - Diameter of the annotation circle
 * @returns {HTMLElement} The created popup element
 */
function createCommentPopup(annotation, index, cx, cy, diameter) {
  const popup = document.createElement('div');
  popup.className = 'comment-popup';
  popup.id = (annotation.id || `ann-${currentPageId}-${index + 1}`);
  popup.innerHTML = renderCommentContent(index, annotation.text);

  // Set initial position to measure dimensions
  popup.style.left = cx + 'px';
  popup.style.top = (cy + diameter / 2 + 10) + 'px';
  popup.style.visibility = 'hidden'; // Hide initially to measure
  popup.style.display = 'block';

  // Check if comment contains an image or is longer than 600 characters
  // Only apply for desktop version
  let hasWidthAdjustment = false;
  const hasImage = annotation.text && annotation.text.includes('![');
  const isLong = annotation.text && annotation.text.length > 600;

  if (!isMobile && (hasImage || isLong)) {
    hasWidthAdjustment = true;
    // Get the image width and set popup width to 60% of it
    const img = document.getElementById('page-image');
    if (img) {
      const imageWidth = img.width;
      popup.style.width = (imageWidth * 0.6) + 'px';
      popup.style.maxWidth = 'none'; // Remove max-width constraint
    }
  }

  // Initialize dataset attributes for tracking hover and click states
  popup.dataset.hoverShown = 'false';
  popup.dataset.clickShown = 'false';

  // Prevent popup from closing when clicking on it
  popup.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  // Append to DOM temporarily to get dimensions
  document.body.appendChild(popup);

  // Function to position the popup based on its actual dimensions
  const positionPopup = (finalHeight) => {
    // Get popup dimensions and viewport height
    const popupRect = popup.getBoundingClientRect();
    const actualHeight = finalHeight || popupRect.height;
    const viewportHeight = window.innerHeight;

    // Calculate space available below and above the annotation
    const spaceBelow = viewportHeight - (cy + diameter / 2 + 10);
    const spaceAbove = cy - diameter / 2 - 10;

    console.log(`Positioning popup ${popup.id} with height ${actualHeight}px`);
    console.log(`Space below: ${spaceBelow}px, Space above: ${spaceAbove}px`);

    // Calculate if the popup would extend beyond the top of the viewport if positioned above
    const wouldExtendBeyondTop = (cy - diameter / 2 - actualHeight - 20) < 0;
    console.log(`Would extend beyond top: ${wouldExtendBeyondTop}`);

    // Determine if popup should be positioned above or below
    // Position below if:
    // 1. It would extend beyond the top of the viewport when positioned above, or
    // 2. It's not an image and fits below, or
    // 3. It's not an image, annotation is in upper half, and there's more space below
    if (wouldExtendBeyondTop || 
        (!hasImage && actualHeight <= spaceBelow) || 
        (!hasImage && cy < viewportHeight / 2 && spaceBelow >= spaceAbove)) {
      // Position below the annotation
      if (hasWidthAdjustment) {
        popup.style.top = (cy + diameter / 2 + 10) + 'px';
      }
      // Remove the above class if it exists
      popup.classList.remove('above');
      console.log(`Positioned popup ${popup.id} below annotation`);
    } else {
      // Position above the annotation circle
      // Calculate position to place the popup above the annotation with proper spacing
      popup.style.top = (cy - diameter / 2 - actualHeight - 20) + 'px';
      popup.style.bottom = 'auto'; // Clear any bottom property
      // Add class to flip the arrow direction
      popup.classList.add('above');
      console.log(`Positioned popup ${popup.id} above annotation`);
    }

    // Store the popup's dimensions for later use when showing/hiding
    popup.dataset.height = actualHeight;
    popup.dataset.positionY = popup.style.top;
  };

  // If the popup contains images, wait for them to load before finalizing position
  if (hasImage) {
    const images = popup.querySelectorAll('img');
    let loadedImages = 0;
    const totalImages = images.length;

    console.log(`Popup ${popup.id} contains ${totalImages} images, waiting for them to load...`);

    // Set a timeout in case images take too long to load
    const positioningTimeout = setTimeout(() => {
      if (loadedImages < totalImages) {
        console.warn(`Timeout reached while waiting for images in popup ${popup.id}. Positioning with current dimensions.`);
        positionPopup();
      }
    }, 3000); // 3 second timeout

    // Add load event listeners to all images
    images.forEach(img => {
      // If image is already loaded
      if (img.complete) {
        loadedImages++;
        console.log(`Image ${loadedImages}/${totalImages} in popup ${popup.id} was already loaded`);
        if (loadedImages === totalImages) {
          clearTimeout(positioningTimeout);
          // Get the updated height after all images are loaded
          const updatedHeight = popup.getBoundingClientRect().height;
          positionPopup(updatedHeight);

          // If the popup is currently visible, update the spacer
          if (popup.style.display === 'block') {
            ensurePopupVisibility(popup, cy, diameter);
          }
        }
      } else {
        // Add load event listener
        img.addEventListener('load', () => {
          loadedImages++;
          console.log(`Image ${loadedImages}/${totalImages} in popup ${popup.id} loaded`);
          if (loadedImages === totalImages) {
            clearTimeout(positioningTimeout);
            // Get the updated height after all images are loaded
            const updatedHeight = popup.getBoundingClientRect().height;
            positionPopup(updatedHeight);

            // If the popup is currently visible, update the spacer
            if (popup.style.display === 'block') {
              ensurePopupVisibility(popup, cy, diameter);
            }
          }
        });

        // Add error event listener in case image fails to load
        img.addEventListener('error', () => {
          loadedImages++;
          console.warn(`Image ${loadedImages}/${totalImages} in popup ${popup.id} failed to load`);
          if (loadedImages === totalImages) {
            clearTimeout(positioningTimeout);
            positionPopup();

            // If the popup is currently visible, update the spacer
            if (popup.style.display === 'block') {
              ensurePopupVisibility(popup, cy, diameter);
            }
          }
        });
      }
    });
  } else {
    // No images, position immediately
    positionPopup();
  }

  // Add event listeners to handle popup visibility changes
  const originalDisplay = popup.style.display;

  // Create a MutationObserver to watch for display changes
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.attributeName === 'style') {
        const currentDisplay = popup.style.display;
        // If popup becomes visible
        if (currentDisplay === 'block' && !popup.dataset.spacerAdded) {
          ensurePopupVisibility(popup, cy, diameter);
        }
        // If popup becomes hidden
        else if (currentDisplay === 'none' && popup.dataset.spacerAdded === 'true') {
          removePopupSpacer(popup);
        }
      }
    });
  });

  // Start observing the popup for display changes
  observer.observe(popup, { attributes: true });

  // Remove from body - it will be added to the proper container later
  document.body.removeChild(popup);
  popup.style.visibility = 'visible';
  popup.style.display = 'none'; // Reset display to none (will be shown on hover/click)

  return popup;
}

/**
 * Renders content for the mobile comment overlay
 * @param {number} index - The index of the annotation
 * @param {string} text - The text content of the annotation
 * @returns {string} HTML content for the mobile overlay
 */
function renderMobileCommentContent(index, text) {
  return `
    <h3>Комментарий ${index + 1}</h3>
    <p>${formatCommentText(text)}</p>
    <div style="margin-top:20px;text-align:center;">
      <button id="scroll-to-global" style="background:#DC143C;color:white;border:none;padding:10px 15px;border-radius:5px;font-weight:bold;cursor:pointer;">
        Показать общий комментарий
      </button>
    </div>
  `;
}

/**
 * Ensures that a popup is fully visible by adjusting the document's scrollable area if needed
 * @param {HTMLElement} popup - The popup element
 * @param {number} cy - Y coordinate of the annotation
 * @param {number} diameter - Diameter of the annotation circle
 */
function ensurePopupVisibility(popup, cy, diameter) {
  // Get the popup's position and dimensions
  const popupRect = popup.getBoundingClientRect();
  const viewportHeight = window.innerHeight;
  const documentHeight = Math.max(
    document.body.scrollHeight,
    document.body.offsetHeight,
    document.documentElement.clientHeight,
    document.documentElement.scrollHeight,
    document.documentElement.offsetHeight
  );

  // Check if the popup extends beyond the bottom of the viewport
  if (popupRect.bottom > viewportHeight) {
    // Calculate how much additional space is needed
    const extraSpace = popupRect.bottom - viewportHeight + 50; // Add some padding

    // Check if a spacer already exists for this popup
    if (popup.dataset.spacerAdded === 'true' && popup.dataset.spacerId) {
      // Update the existing spacer
      const spacer = document.getElementById(popup.dataset.spacerId);
      if (spacer) {
        // Only update if the new height is greater than the current height
        const currentHeight = parseInt(spacer.style.height, 10);
        if (extraSpace > currentHeight) {
          spacer.style.height = extraSpace + 'px';
          console.log(`Updated spacer for popup ${popup.id} with new height ${extraSpace}px (was ${currentHeight}px)`);
        }
      } else {
        // Spacer doesn't exist anymore, create a new one
        createNewSpacer();
      }
    } else {
      // Create a new spacer
      createNewSpacer();
    }

    function createNewSpacer() {
      // Create a spacer element to extend the document height
      const spacer = document.createElement('div');
      spacer.id = 'popup-spacer-' + popup.id;
      spacer.style.height = extraSpace + 'px';
      spacer.style.width = '100%';
      spacer.style.position = 'relative';
      spacer.style.clear = 'both';
      spacer.style.zIndex = '-1';

      // Add the spacer to the layout container instead of body
      const layoutContainer = document.getElementById('layout');
      if (layoutContainer) {
        layoutContainer.appendChild(spacer);
      } else {
        document.body.appendChild(spacer);
      }

      // Mark the popup as having a spacer
      popup.dataset.spacerAdded = 'true';
      popup.dataset.spacerId = spacer.id;

      console.log(`Added spacer for popup ${popup.id} with height ${extraSpace}px`);
    }
  }
}

/**
 * Removes the spacer element for a popup
 * @param {HTMLElement} popup - The popup element
 */
function removePopupSpacer(popup) {
  if (popup.dataset.spacerAdded === 'true' && popup.dataset.spacerId) {
    const spacer = document.getElementById(popup.dataset.spacerId);
    if (spacer) {
      spacer.remove();
      console.log(`Removed spacer for popup ${popup.id}`);
    }

    // Reset the spacer flags
    popup.dataset.spacerAdded = 'false';
    delete popup.dataset.spacerId;
  }
}

/**
 * Updates the sidebar comment list with an annotation
 * @param {number} index - The index of the annotation
 * @param {string} text - The text content of the annotation
 */
function updateCommentSidebar(index, text) {
  const listUl = document.getElementById('comment-list');
  if (!listUl) return;

  listUl.innerHTML = '';
  const li = document.createElement('li');
  li.innerHTML = '<strong>' + (index + 1) + '.</strong> ' + formatCommentText(text);
  listUl.appendChild(li);
}
