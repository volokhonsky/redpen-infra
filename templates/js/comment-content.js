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

  // Initialize dataset attributes for tracking hover and click states
  popup.dataset.hoverShown = 'false';
  popup.dataset.clickShown = 'false';

  // Prevent popup from closing when clicking on it
  popup.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  // Append to DOM temporarily to get dimensions
  document.body.appendChild(popup);

  // Check if popup extends beyond viewport bottom
  const popupRect = popup.getBoundingClientRect();
  const viewportHeight = window.innerHeight;

  // If popup extends beyond viewport bottom, position it above the annotation
  if (popupRect.bottom > viewportHeight) {
    // Position above the annotation circle instead of below
    popup.style.top = (cy - diameter / 2 - popup.offsetHeight - 10) + 'px';
    // Add class to flip the arrow direction
    popup.classList.add('above');
  } else {
    // Keep original position below the annotation
    popup.style.top = (cy + diameter / 2 + 10) + 'px';
    // Remove the above class if it exists
    popup.classList.remove('above');
  }

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
