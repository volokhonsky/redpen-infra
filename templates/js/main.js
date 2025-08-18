// Updated main.js to use metadata.json for pagination and document info
/**
 * Main JavaScript file for RedPen application
 * Now loads metadata.json to get totalPages, defaultPage, and other info
 */

let overlayContainer;
let currentPageId;
let allAnns = [];
let isMobile = false;
let resizeTimeout;
let metadata = null;

/**
 * Initialize the application
 */
async function init() {
  // Set initial mobile flag
  isMobile = checkMobile();

  // Load document metadata
  try {
    const res = await fetch('metadata.json');
    metadata = await res.json();
  } catch (e) {
    console.error('Failed to load metadata.json', e);
    metadata = { totalPages: 1, defaultPage: 1 };
  }

  // Add event listener for window resize
  window.addEventListener('resize', () => {
    isMobile = checkMobile();
    updateLayout();

    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      repositionAnnotations();
    }, 250);
  });

  // Check for hash in URL to determine initial page
  const hash = window.location.hash;
  let startPage = 1;

  if (hash && hash.startsWith('#page')) {
    const pageNum = parseInt(hash.substring(5));
    if (!isNaN(pageNum) && pageNum >= 1 && pageNum <= (metadata.totalPages || 1)) {
      startPage = pageNum;
    }
  } else if (metadata.pageNumbering && metadata.pageNumbering.logicalStart) {
    // Use logical start page from metadata if available
    startPage = metadata.pageNumbering.logicalStart;
  }

  // Load the initial page
  loadPage(startPage);

  // Listen for hash changes
  window.addEventListener('hashchange', () => {
    const hash = window.location.hash;
    if (hash && hash.startsWith('#page')) {
      const pageNum = parseInt(hash.substring(5));
      if (!isNaN(pageNum) && pageNum >= 1 && pageNum <= (metadata.totalPages || 1)) {
        loadPage(pageNum);
      }
    }
  });

  // Initialize mobile-specific functionality
  if (typeof initMobile === 'function') {
    initMobile();
  }
}

/**
 * Load a page with the given logical page number
 * @param {number} pageNum - Logical page number to load
 */
async function loadPage(pageNum) {
  isMobile = checkMobile();
  closeMobileOverlay();

  document.querySelectorAll('.comment-popup').forEach(popup => popup.remove());
  document.querySelectorAll('.circle').forEach(circle => circle.remove());

  document.getElementById('image-container').style.display = 'flex';
  document.getElementById('global-comment-container').style.display = 'block';

  // Compute physical page: logicalStart + pageNum - 1
  const phys = (metadata.pageNumbering.physicalStart || 1) + (pageNum - 1);
  currentPageId = 'page_' + String(phys).padStart(3, '0');
  const img = document.getElementById('page-image');
  img.src = 'images/' + currentPageId + '.png';

  await new Promise(r => { img.onload = r; });
  await new Promise(r => setTimeout(r, 50));

  if (overlayContainer) overlayContainer.remove();
  overlayContainer = document.createElement('div');
  overlayContainer.id = 'overlay-container';
  overlayContainer.style.top = img.offsetTop + 'px';
  overlayContainer.style.left = img.offsetLeft + 'px';
  overlayContainer.style.width = img.width + 'px';
  overlayContainer.style.height = img.height + 'px';
  overlayContainer.style.pointerEvents = 'auto';

  document.getElementById('image-container').appendChild(overlayContainer);
  updateLayout();
  setTimeout(updateLayout, 100);

  try {
    allAnns = await fetch('annotations/' + currentPageId + '.json').then(r => r.json());
  } catch (e) {
    allAnns = [];
  }

  const globalContainer = document.getElementById('global-comment-container');
  const globalDiv = document.getElementById('global-comment');
  const generalAnns = allAnns.filter(a => a.annType === 'general');
  const mainAnns = allAnns.filter(a => a.annType === 'main');

  globalContainer.style.display = 'block';
  globalContainer.style.border = '3px solid #DC143C';

  let globalContent = '';
  if (generalAnns.length) {
    globalContent += generalAnns.map(a => `<p>${formatCommentText(a.text)}</p>`).join('');
  }
  if (mainAnns.length) {
    globalContent += mainAnns.map((a,i) => `<p><strong>${i+1}.</strong> ${formatCommentText(a.text)}</p>`).join('');
  }
  globalDiv.innerHTML = globalContent || 'Нет общего комментария.';

  // Notify editor (if present) about loaded annotations for this page
  try { if (window.RedPenEditor && typeof window.RedPenEditor.onAnnotationsLoaded === 'function') { window.RedPenEditor.onAnnotationsLoaded(allAnns || []); } } catch(e) { /* noop */ }

  document.getElementById('comment-list').innerHTML = '';
  repositionAnnotations();

  // Update pagination
  updatePagination(pageNum);

  // Update mobile pagination if function exists
  if (typeof updateMobilePagination === 'function') {
    updateMobilePagination();
  }
}

/**
 * Jump to next page
 */
function nextPage() {
  const total = metadata.totalPages || 1;
  const currentLogical = logicalFromPhysical();
  if (currentLogical < total) loadPage(currentLogical + 1);
}

/**
 * Jump to previous page
 */
function prevPage() {
  const currentLogical = logicalFromPhysical();
  if (currentLogical > 1) loadPage(currentLogical - 1);
}

/**
 * Go to a specific page entered by the user
 */
function goToPage() {
  const input = document.getElementById('page-input');
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
 * Convert currentPageId (physical) to logical page number
 */
function logicalFromPhysical() {
  const phys = parseInt(currentPageId.split('_')[1], 10);
  return phys - (metadata.pageNumbering.physicalStart || 1) + 1;
}

/**
 * Update pagination based on current page and total pages
 * @param {number} currentPage - Current logical page number
 */
function updatePagination(currentPage) {
  const pageNumbersContainer = document.getElementById('page-numbers');
  if (!pageNumbersContainer) return;

  // Clear existing page numbers
  pageNumbersContainer.innerHTML = '';

  // Get total pages from metadata
  const totalPages = metadata.totalPages || 1;

  // Determine which page numbers to show
  // Always show first page, last page, current page, and pages around current page
  const pagesToShow = new Set();
  pagesToShow.add(1); // First page
  pagesToShow.add(totalPages); // Last page

  // Add current page and pages around it
  for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages, currentPage + 2); i++) {
    pagesToShow.add(i);
  }

  // Convert to sorted array
  const pageNumbers = Array.from(pagesToShow).sort((a, b) => a - b);

  // Add page numbers with ellipses for gaps
  let prevPage = 0;
  pageNumbers.forEach(pageNum => {
    if (pageNum - prevPage > 1) {
      // Add ellipsis for gap
      const ellipsis = document.createElement('span');
      ellipsis.className = 'page-ellipsis';
      ellipsis.textContent = '...';
      pageNumbersContainer.appendChild(ellipsis);
    }

    // Add page number
    const pageElement = document.createElement('a');
    pageElement.className = 'page-number' + (pageNum === currentPage ? ' active' : '');
    pageElement.textContent = pageNum;
    pageElement.href = '#';
    pageElement.onclick = function() {
      loadPage(pageNum);
      return false;
    };
    pageNumbersContainer.appendChild(pageElement);

    prevPage = pageNum;
  });

  // Update prev/next buttons
  const prevButton = document.getElementById('prev-page');
  const nextButton = document.getElementById('next-page');

  if (prevButton) {
    prevButton.style.visibility = currentPage > 1 ? 'visible' : 'hidden';
  }

  if (nextButton) {
    nextButton.style.visibility = currentPage < totalPages ? 'visible' : 'hidden';
  }
}

document.addEventListener('DOMContentLoaded', init);
