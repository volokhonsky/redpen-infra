/**
 * Mobile-specific functionality for RedPen application
 */

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

  // Log for debugging
  console.log('Mobile overlay closed, global comment container:', globalCommentContainer);
  console.log('Global comment container display:', globalCommentContainer.style.display);
  console.log('Global comment container visibility:', window.getComputedStyle(globalCommentContainer).visibility);
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

  mobileContent.innerHTML = `<h3>Комментарий ${index+1}</h3><p>${text}</p>`;

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

  // Add a button to scroll to the global comment
  mobileContent.innerHTML += `
    <div style="margin-top:20px;text-align:center;">
      <button id="scroll-to-global" style="background:#DC143C;color:white;border:none;padding:10px 15px;border-radius:5px;font-weight:bold;cursor:pointer;">
        Показать общий комментарий
      </button>
    </div>
  `;

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