/**
 * Layout and responsive behavior for RedPen application
 */

/**
 * Check if the device is mobile based on window width
 * @returns {boolean} True if the device is mobile, false otherwise
 */
function checkMobile() {
  return window.innerWidth <= 768;
}

/**
 * Update layout based on image width and window size
 */
function updateLayout() {
  const img = document.getElementById('page-image');
  const layout = document.getElementById('layout');
  const imageContainer = document.getElementById('image-container');
  const contentWrapper = document.getElementById('content-wrapper');
  const globalCommentContainer = document.getElementById('global-comment-container');

  // Always ensure image container is visible
  imageContainer.style.display = 'flex';

  // Always ensure global comment container is visible
  globalCommentContainer.style.display = 'block';

  // Medium screens (500px-1023px): Always use column layout
  if (window.innerWidth >= 500 && window.innerWidth < 1024) {
    layout.style.flexDirection = 'column';
    contentWrapper.style.flexDirection = 'column';
    return; // Let CSS media queries handle the rest
  }

  // Check if image is loaded and has a width
  if (img.complete && img.offsetWidth > 0) {
    if (img.offsetWidth < 500) {
      layout.style.flexDirection = 'column';
      contentWrapper.style.flexDirection = 'column';
    } else if (window.innerWidth >= 1024 || (window.innerWidth >= 768 && window.matchMedia('(orientation: landscape)').matches)) {
      layout.style.flexDirection = 'row';
      contentWrapper.style.flexDirection = 'row';
    } else {
      layout.style.flexDirection = 'column';
      contentWrapper.style.flexDirection = 'column';
    }
  } else {
    // If image is not loaded yet, use window width as fallback
    if (window.innerWidth < 500) {
      layout.style.flexDirection = 'column';
      contentWrapper.style.flexDirection = 'column';
    } else if (window.innerWidth >= 1024 || (window.innerWidth >= 768 && window.matchMedia('(orientation: landscape)').matches)) {
      layout.style.flexDirection = 'row';
      contentWrapper.style.flexDirection = 'row';
    } else {
      layout.style.flexDirection = 'column';
      contentWrapper.style.flexDirection = 'column';
    }
  }
}