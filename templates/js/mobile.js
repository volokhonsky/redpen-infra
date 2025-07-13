/**
 * Mobile-specific functionality for RedPen application
 */

let touchStartX = 0;
let touchEndX = 0;
let interactionTimeout;
let navButtonsVisible = false;

/**
 * Initialize mobile-specific functionality
 */
function initMobile() {
  if (!checkMobile()) return;

  // Set up swipe detection
  const imageContainer = document.getElementById('image-container');
  if (imageContainer) {
    imageContainer.addEventListener('touchstart', handleTouchStart, false);
    imageContainer.addEventListener('touchend', handleTouchEnd, false);

    // Set up interaction detection for nav buttons
    imageContainer.addEventListener('touchmove', showNavButtons, false);
    imageContainer.addEventListener('click', showNavButtons, false);
  }

  // Set up mobile page input
  const mobilePageInput = document.getElementById('mobile-page-input');
  if (mobilePageInput) {
    mobilePageInput.addEventListener('keypress', function(event) {
      if (event.key === 'Enter') {
        goToMobilePage();
        event.preventDefault();
      }
    });
  }

  // Update mobile pagination info
  updateMobilePagination();
}

/**
 * Update mobile pagination information
 */
function updateMobilePagination() {
  if (!checkMobile()) return;

  const currentPageNumber = document.getElementById('current-page-number');
  const totalPagesNumber = document.getElementById('total-pages-number');

  if (currentPageNumber && totalPagesNumber && metadata) {
    const currentLogical = logicalFromPhysical();
    const totalPages = metadata.totalPages || 1;

    currentPageNumber.textContent = currentLogical;
    totalPagesNumber.textContent = totalPages;

    // Update navigation buttons visibility based on current page
    const prevButton = document.getElementById('mobile-prev-button');
    if (prevButton) {
      prevButton.style.display = currentLogical > 1 ? 'flex' : 'none';
    }
  }
}

/**
 * Go to a specific page from mobile input
 */
function goToMobilePage() {
  const input = document.getElementById('mobile-page-input');
  if (!input) return;

  const pageNum = parseInt(input.value);
  const totalPages = metadata.totalPages || 1;

  if (!isNaN(pageNum) && pageNum >= 1 && pageNum <= totalPages) {
    loadPage(pageNum);
    input.value = ''; // Clear the input after navigation
  } else {
    alert(`Пожалуйста, введите номер страницы от 1 до ${totalPages}`);
  }
}

/**
 * Handle touch start event for swipe detection
 * @param {TouchEvent} event - The touch event
 */
function handleTouchStart(event) {
  touchStartX = event.changedTouches[0].screenX;
}

/**
 * Handle touch end event for swipe detection
 * @param {TouchEvent} event - The touch event
 */
function handleTouchEnd(event) {
  touchEndX = event.changedTouches[0].screenX;
  handleSwipe();
}

/**
 * Handle swipe gesture
 */
function handleSwipe() {
  const swipeThreshold = 50; // Minimum distance for a swipe

  if (touchEndX < touchStartX - swipeThreshold) {
    // Swipe left - go to next page
    nextPage();
  }

  if (touchEndX > touchStartX + swipeThreshold) {
    // Swipe right - go to previous page
    prevPage();
  }
}

/**
 * Show navigation buttons on interaction
 */
function showNavButtons() {
  if (!checkMobile()) return;

  const navButtons = document.getElementById('mobile-nav-buttons');
  if (!navButtons) return;

  // Make buttons visible
  navButtons.classList.add('visible');
  navButtonsVisible = true;

  // Hide buttons after a delay
  clearTimeout(interactionTimeout);
  interactionTimeout = setTimeout(() => {
    navButtons.classList.remove('visible');
    navButtonsVisible = false;
  }, 3000); // Hide after 3 seconds
}

/**
 * Close mobile overlay
 */
function closeMobileOverlay() {
  document.getElementById('mobile-overlay').style.display = 'none';
  // Remove document-level click event listener when overlay is closed
  document.removeEventListener('click', handleDocumentClick);

  // Ensure global comment container is visible after closing overlay
  const globalCommentContainer = document.getElementById('global-comment-container');
  globalCommentContainer.style.display = 'block';
  globalCommentContainer.style.visibility = 'visible';
  globalCommentContainer.style.opacity = '1';
}

/**
 * Handle document clicks for mobile overlay
 * @param {Event} event - The click event
 */
function handleDocumentClick(event) {
  const mobileOverlay = document.getElementById('mobile-overlay');
  const mobileContent = document.getElementById('mobile-comment-content');
  const closeButton = document.querySelector('.mobile-overlay-close');

  // If overlay is visible and click is outside the content
  if (mobileOverlay.style.display === 'block' && 
      !mobileContent.contains(event.target) && 
      !event.target.closest('.circle') &&
      event.target !== closeButton &&
      !mobileOverlay.contains(event.target)) {
    closeMobileOverlay();
  }
}

/**
 * Handle clicks on the mobile overlay
 * @param {Event} event - The click event
 */
function handleOverlayClick(event) {
  // If the click is directly on the overlay (not on its children)
  if (event.target === document.getElementById('mobile-overlay')) {
    closeMobileOverlay();
    // Prevent the event from bubbling up
    event.stopPropagation();
  }
}

/**
 * Show comment in mobile overlay
 * @param {number} index - The index of the comment
 * @param {string} text - The text of the comment
 */
function showMobileComment(index, text) {
  const mobileOverlay = document.getElementById('mobile-overlay');
  const mobileContent = document.getElementById('mobile-comment-content');
  const globalCommentContainer = document.getElementById('global-comment-container');

  // Use the renderMobileCommentContent function from comment-content.js
  mobileContent.innerHTML = renderMobileCommentContent(index, text);

  // Prevent clicks on the content from closing the overlay
  mobileContent.addEventListener('click', function(event) {
    event.stopPropagation();
  });

  // Remove any existing click event listeners
  mobileOverlay.removeEventListener('click', handleOverlayClick);

  // Add click event to close when clicking outside the content
  mobileOverlay.addEventListener('click', handleOverlayClick);

  // Ensure global comment container is visible
  globalCommentContainer.style.display = 'block';
  globalCommentContainer.style.visibility = 'visible';
  globalCommentContainer.style.opacity = '1';

  // Add click event to the button
  setTimeout(() => {
    const scrollButton = document.getElementById('scroll-to-global');
    if (scrollButton) {
      scrollButton.addEventListener('click', function(event) {
        event.stopPropagation();
        scrollToGlobalComment();
        closeMobileOverlay();
      });
    }
  }, 10);

  // Show the overlay
  mobileOverlay.style.display = 'block';

  // Add document-level click listener with a slight delay to avoid immediate closure
  setTimeout(() => {
    document.addEventListener('click', handleDocumentClick);
  }, 10);

  // Log for debugging
  console.log('Mobile overlay shown, global comment container:', globalCommentContainer);
  console.log('Global comment container display:', globalCommentContainer.style.display);
  console.log('Global comment container visibility:', window.getComputedStyle(globalCommentContainer).visibility);
}
