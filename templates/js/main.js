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

function isEditorMode() {
  try {
    if (window.hasEditorFlag && typeof window.hasEditorFlag === 'function') {
      return !!window.hasEditorFlag();
    }
    const usp = new URLSearchParams(window.location.search || '');
    const hsp = new URLSearchParams((window.location.hash || '').replace(/^#/, ''));
    const qp = usp.get('editor');
    const hp = hsp.get('editor');
    return (qp === '1' || qp === 'true') || (hp === '1' || hp === 'true') || window.REDPEN_EDITOR === true;
  } catch (e) {
    return window.REDPEN_EDITOR === true;
  }
}

function clampPage(n) {
  const total = (metadata && metadata.totalPages) ? metadata.totalPages : 1;
  n = parseInt(n, 10);
  if (isNaN(n)) n = 1;
  if (n < 1) n = 1;
  if (n > total) n = total;
  return n;
}

function buildPageUrl(n) {
  const url = new URL(window.location.href);
  url.searchParams.set('page', String(n));
  if (isEditorMode()) url.searchParams.set('editor', '1'); else url.searchParams.delete('editor');
  return url.pathname + url.search + url.hash;
}

function navigateTo(targetPage) {
  const n = clampPage(targetPage);
  const href = buildPageUrl(n);
  history.pushState({ page: n }, '', href);
  loadPage(n);
}

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

  // Determine initial page from query (?page) then hash (#page)
  const usp = new URLSearchParams(window.location.search || '');
  const pageFromQuery = parseInt(usp.get('page'), 10);
  let startPage = !isNaN(pageFromQuery) ? pageFromQuery : undefined;
  if (typeof startPage === 'undefined') {
    const hash = window.location.hash;
    if (hash && hash.startsWith('#page')) {
      const pageNum = parseInt(hash.substring(5), 10);
      if (!isNaN(pageNum)) startPage = pageNum;
    }
  }
  if (typeof startPage === 'undefined') {
    if (metadata.pageNumbering && metadata.pageNumbering.logicalStart) {
      startPage = metadata.pageNumbering.logicalStart;
    } else {
      startPage = 1;
    }
  }
  startPage = clampPage(startPage);

  // Load the initial page
  loadPage(startPage);

  // Listen for Back/Forward navigation
  window.addEventListener('popstate', () => {
    const usp2 = new URLSearchParams(window.location.search || '');
    const p = parseInt(usp2.get('page'), 10);
    loadPage(clampPage(isNaN(p) ? 1 : p));
  });

  // Delegate clicks on pagination to navigate without full reload
  const pagination = document.getElementById('pagination');
  if (pagination) {
    pagination.addEventListener('click', (e) => {
      const a = e.target && e.target.closest ? e.target.closest('a') : null;
      if (!a) return;
      const href = a.getAttribute('href');
      if (!href) return;
      try {
        const u = new URL(href, window.location.href);
        const pStr = u.searchParams.get('page');
        if (pStr) {
          e.preventDefault();
          const n = parseInt(pStr, 10);
          navigateTo(n);
        }
      } catch (_) { /* ignore */ }
    });
  }

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
  if (currentLogical < total) navigateTo(currentLogical + 1);
}

/**
 * Jump to previous page
 */
function prevPage() {
  const currentLogical = logicalFromPhysical();
  if (currentLogical > 1) navigateTo(currentLogical - 1);
}

/**
 * Go to a specific page entered by the user
 */
function goToPage() {
  const input = document.getElementById('page-input');
  const pageNum = parseInt(input.value);
  const totalPages = metadata.totalPages || 1;

  if (!isNaN(pageNum)) {
    const clamped = Math.max(1, Math.min(totalPages, pageNum));
    navigateTo(clamped);
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

  // Helper to create URL for page
  const hrefFor = (n) => buildPageUrl(n);

  // Add page numbers with ellipses for gaps
  let prevPageNum = 0;
  pageNumbers.forEach(pageNum => {
    if (pageNum - prevPageNum > 1) {
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
    pageElement.href = hrefFor(pageNum);
    // No inline onclick; use delegated handler for SPA navigation
    pageNumbersContainer.appendChild(pageElement);

    prevPageNum = pageNum;
  });

  // Update prev/next buttons
  const prevButton = document.getElementById('prev-page');
  const nextButton = document.getElementById('next-page');

  if (prevButton) {
    prevButton.style.visibility = currentPage > 1 ? 'visible' : 'hidden';
    prevButton.setAttribute('href', hrefFor(Math.max(1, currentPage - 1)));
  }

  if (nextButton) {
    nextButton.style.visibility = currentPage < totalPages ? 'visible' : 'hidden';
    nextButton.setAttribute('href', hrefFor(Math.min(totalPages, currentPage + 1)));
  }
}

document.addEventListener('DOMContentLoaded', init);
