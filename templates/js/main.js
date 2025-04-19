/**
 * Main JavaScript file for RedPen application
 * Contains core functionality and initialization
 */

// Global variables
let overlayContainer, currentPageId, allAnns = [], isMobile = false, resizeTimeout;

/**
 * Initialize the application
 */
function init() {
  // Set initial mobile flag
  isMobile = checkMobile();

  // Add event listener for window resize
  window.addEventListener('resize', function() {
    isMobile = checkMobile();
    updateLayout();

    // Debounce the annotation repositioning to prevent too many updates during resizing
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(function() {
      repositionAnnotations();
    }, 250); // Wait 250ms after resize ends before repositioning
  });

  // Load the initial page
  loadPage(7);
}

/**
 * Load a page with the given page number
 * @param {number} pageNum - The page number to load
 */
async function loadPage(pageNum) {
  // Set mobile flag
  isMobile = checkMobile();

  // Close mobile overlay if it's open
  closeMobileOverlay();

  // Remove any existing comment popups and circles
  document.querySelectorAll('.comment-popup').forEach(popup => popup.remove());
  document.querySelectorAll('.circle').forEach(circle => circle.remove());

  // Always ensure image container is visible from the start
  document.getElementById('image-container').style.display = 'flex';

  // Always ensure global comment container is visible from the start
  document.getElementById('global-comment-container').style.display = 'block';

  currentPageId = 'page_' + String(pageNum).padStart(3,'0');
  const img = document.getElementById('page-image');
  img.src = 'redpen-content/images/' + currentPageId + '.png';

  // Wait for image to load
  await new Promise(r => {
    img.onload = r;
  });

  // Add a small delay to ensure the image is fully rendered
  await new Promise(resolve => setTimeout(resolve, 50));

  if (overlayContainer) overlayContainer.remove();
  overlayContainer = document.createElement('div');
  overlayContainer.style.position = 'absolute';
  overlayContainer.style.top  = img.offsetTop + 'px';
  overlayContainer.style.left = img.offsetLeft + 'px';
  overlayContainer.style.width  = img.width  + 'px';
  overlayContainer.style.height = img.height + 'px';
  overlayContainer.style.pointerEvents = 'auto';

  // Force image container to be visible again
  const imageContainer = document.getElementById('image-container');
  imageContainer.style.display = 'flex';
  imageContainer.appendChild(overlayContainer);

  // Update layout based on image width
  updateLayout();

  // Double-check layout after a short delay
  setTimeout(updateLayout, 100);

  // Load annotations
  try {
    allAnns = await fetch('redpen-content/annotations/' + currentPageId + '.json').then(r=>r.json());
  } catch(e){ allAnns = [] }

  // Global comment
  const globalDiv = document.getElementById('global-comment');
  const globalContainer = document.getElementById('global-comment-container');
  const mainAnns = allAnns.filter(a=>a.annType==='main');

  // Force global comment container to be visible
  globalContainer.style.display = 'block';

  // Add a border to make it more visible for debugging
  globalContainer.style.border = '3px solid #DC143C';

  if (mainAnns.length) {
    globalDiv.innerHTML = mainAnns.map((a,i)=>'<p><strong>'+(i+1)+'.</strong> '+a.text+'</p>').join('');
  } else {
    globalDiv.textContent = 'Нет общего комментария.';
  }

  // Log for debugging
  console.log('Global comment container:', globalContainer);
  console.log('Global comment container display:', globalContainer.style.display);
  console.log('Global comment container visibility:', window.getComputedStyle(globalContainer).visibility);
  console.log('Global comment container height:', window.getComputedStyle(globalContainer).height);

  const listUl = document.getElementById('comment-list');
  listUl.innerHTML = '';

  // Position annotations using the repositionAnnotations function
  repositionAnnotations();
}

/**
 * Function to scroll to the global comment
 */
function scrollToGlobalComment() {
  const globalCommentContainer = document.getElementById('global-comment-container');
  if (globalCommentContainer) {
    // Ensure the global comment is visible
    globalCommentContainer.style.display = 'block';
    globalCommentContainer.style.visibility = 'visible';
    globalCommentContainer.style.opacity = '1';

    // Add a highlight effect to make it more noticeable
    globalCommentContainer.style.transition = 'background-color 0.5s';
    globalCommentContainer.style.backgroundColor = '#ffdddd';

    // Scroll to the global comment with smooth behavior
    globalCommentContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });

    // Reset the background color after a delay
    setTimeout(() => {
      globalCommentContainer.style.backgroundColor = '';
    }, 1500);

    console.log('Scrolled to global comment container');
  }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', init);
