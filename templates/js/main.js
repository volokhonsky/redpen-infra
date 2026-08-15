// Updated main.js to use metadata.json for pagination and document info
/**
 * Main JavaScript file for RedPen application
 * Now loads metadata.json to get totalPages, defaultPage, and other info
 */

let overlayContainer;
let currentPageId;
let currentPageNum = undefined;  // ✅ ДОБАВИТЬ
let currentPageLabel;
let currentDocId = null;
let allAnns = [];
let isMobile = false;
let resizeTimeout;
let metadata = null;


function getDocIdFromPath() {
  try {
    const pathParts = window.location.pathname.split('/').filter(p => p.length > 0);
    if (pathParts.length >= 2) {
      const docId = pathParts[pathParts.length - 2];
      if (/^[a-zA-Z0-9_-]+$/.test(docId)) {
        return docId;
      }
    }
  } catch(e) { /* noop */ }
  return null;
}

/** Read a URL parameter from either the query string or the hash, in that
 * order. Every mode flag below is addressable both ways. */
function getUrlParam(name) {
  try {
    const usp = new URLSearchParams(window.location.search || '');
    const fromQuery = usp.get(name);
    if (fromQuery !== null) return fromQuery;
    const hsp = new URLSearchParams((window.location.hash || '').replace(/^#/, ''));
    return hsp.get(name);
  } catch (e) {
    return null;
  }
}

function isTruthyParam(value) {
  return value === '1' || value === 'true';
}

function isEditorMode() {
  try {
    if (window.hasEditorFlag && typeof window.hasEditorFlag === 'function') {
      return !!window.hasEditorFlag();
    }
    return isTruthyParam(getUrlParam('editor')) || window.REDPEN_EDITOR === true;
  } catch (e) {
    return window.REDPEN_EDITOR === true;
  }
}

/**
 * Read-only preview mode that reveals draft annotations. Kept as the legacy
 * spelling of ?tags=draft: enabled by ?showDrafts=1 (query or hash), it just
 * lifts the default "draft" exclusion in getTagFilter().
 * Intentionally independent of editor mode: the editor pulls its own live,
 * editable drafts from the API and renders them itself, so this static preview
 * stays off there to avoid double-rendering the same drafts.
 */
function isDraftMode() {
  if (isEditorMode()) return false;
  return isTruthyParam(getUrlParam('showDrafts'));
}

function parseTagList(value) {
  if (!value) return [];
  return value.split(',').map(t => t.trim().toLowerCase()).filter(t => t.length > 0);
}

/**
 * URL-driven annotation filtering. Every annotation carries a `tags` array in
 * the published JSON, and the API's `status` is mirrored there as a `draft`
 * tag -- so drafts are just an ordinary tag, not a separate file.
 *
 *   ?tags=a,b     keep only annotations carrying at least one of a, b
 *   ?notags=a,b   drop annotations carrying any of a, b
 *
 * Exclusion always wins. Drafts are hidden unless the URL mentions `draft`
 * explicitly (in either list) or ?showDrafts=1 is on.
 */
function getTagFilter() {
  const include = parseTagList(getUrlParam('tags'));
  const exclude = parseTagList(getUrlParam('notags'));
  const mentionsDraft = include.indexOf('draft') !== -1 || exclude.indexOf('draft') !== -1;
  if (!mentionsDraft && !isDraftMode()) exclude.push('draft');
  return { include, exclude };
}

/** Whether the URL asks for drafts at all -- by ?showDrafts=1 or by naming
 * `draft` in ?tags=. Used to decide whether the legacy sidecar is worth
 * fetching; the merged page file needs no such decision. */
function draftsRequested() {
  if (isEditorMode()) return false;
  return getTagFilter().exclude.indexOf('draft') === -1;
}

/** Normalize one annotation to a lowercase `tags` array. `draft: true` from
 * the publisher (and from older files that predate tags) becomes the tag. */
function annotationTags(a) {
  const tags = Array.isArray(a.tags) ? a.tags.map(t => String(t).toLowerCase()) : [];
  if (a.draft && tags.indexOf('draft') === -1) tags.unshift('draft');
  return tags;
}

function applyTagFilter(anns) {
  // In editor mode the editor owns what's shown (it renders live drafts from
  // the API); filtering here would fight it.
  if (isEditorMode()) return anns;
  const filter = getTagFilter();
  return anns.filter(a => {
    const tags = annotationTags(a);
    if (filter.exclude.some(t => tags.indexOf(t) !== -1)) return false;
    if (!filter.include.length) return true;
    return filter.include.some(t => tags.indexOf(t) !== -1);
  });
}

function clampPage(n) {
  // Doubles as clampIndex(): in manifest mode metadata.totalPages is always
  // set to metadata.pages.length (see scripts/generate_page_manifest.py), so
  // this clamps into [1, pages.length] there and [1, totalPages] otherwise.
  const total = (metadata && metadata.totalPages) ? metadata.totalPages : 1;
  n = parseInt(n, 10);
  if (isNaN(n)) n = 1;
  if (n < 1) n = 1;
  if (n > total) n = total;
  return n;
}

/**
 * Manifest-based addressing (docs/page-addressing-proposal.md, B.3).
 * Active only when metadata.pages is a non-empty array; every document
 * without it keeps the exact legacy behavior below (physicalStart math,
 * ?page=N, no ?p=).
 */
function manifestMode() {
  return !!(metadata && Array.isArray(metadata.pages) && metadata.pages.length > 0);
}

function fileForIndex(i) {
  return metadata.pages[i - 1].file;
}

function labelForIndex(i) {
  return metadata.pages[i - 1].label;
}

function indexForLabel(label) {
  if (!manifestMode() || label === undefined || label === null) return -1;
  const norm = String(label).trim().toLowerCase();
  if (!norm) return -1;
  for (let i = 0; i < metadata.pages.length; i++) {
    if (String(metadata.pages[i].label).toLowerCase() === norm) return i + 1;
  }
  return -1;
}

function indexForFileKey(fileKey) {
  if (!manifestMode()) return -1;
  const file = 'page_' + fileKey;
  for (let i = 0; i < metadata.pages.length; i++) {
    if (metadata.pages[i].file === file) return i + 1;
  }
  return -1;
}

/** Old physicalStart arithmetic: legacy logical page number -> file key. */
function legacyFileKeyForLogicalPage(n) {
  const phys = (metadata.pageNumbering && metadata.pageNumbering.physicalStart || 1) + (n - 1);
  return String(phys).padStart(3, '0');
}

/**
 * Resolve a legacy ?page=N / #pageN value to a navigation index. In manifest
 * mode this looks up the file the old arithmetic points to and returns its
 * manifest index (so old links keep opening the same file); if that file
 * isn't in the manifest, N is clamped and used directly as an index.
 */
function resolveLegacyPageNumber(n) {
  if (!manifestMode()) return clampPage(n);
  const idx = indexForFileKey(legacyFileKeyForLogicalPage(n));
  return idx !== -1 ? idx : clampPage(n);
}

/** Accepts a label (manifest mode, case-insensitive) or a plain index/page
 * number, as typed into the "go to page" input. Returns NaN if unusable. */
function resolveIndexFromUserInput(raw) {
  const trimmed = String(raw === undefined || raw === null ? '' : raw).trim();
  if (!trimmed) return NaN;
  const byLabel = indexForLabel(trimmed);
  if (byLabel !== -1) return byLabel;
  const n = parseInt(trimmed, 10);
  return isNaN(n) ? NaN : n;
}

/** Resolve the initial index to load from the current URL, in priority
 * order: ?p=<label> (manifest only) -> legacy ?page=N -> legacy #pageN ->
 * default. Mirrors docs/page-addressing-proposal.md section 3.2. */
function resolveStartIndex() {
  const usp = new URLSearchParams(window.location.search || '');

  if (manifestMode()) {
    const pParam = usp.get('p');
    if (pParam !== null && pParam !== '') {
      const idx = indexForLabel(pParam);
      if (idx !== -1) return idx;
    }
  }

  const pageFromQuery = parseInt(usp.get('page'), 10);
  if (!isNaN(pageFromQuery)) return resolveLegacyPageNumber(pageFromQuery);

  const hash = window.location.hash;
  if (hash && hash.startsWith('#page')) {
    const hn = parseInt(hash.substring(5), 10);
    if (!isNaN(hn)) return resolveLegacyPageNumber(hn);
  }

  if (manifestMode()) {
    const idx = indexForLabel('1');
    return idx !== -1 ? idx : 1;
  }
  if (metadata.pageNumbering && metadata.pageNumbering.logicalStart) {
    return clampPage(metadata.pageNumbering.logicalStart);
  }
  return 1;
}

function buildPageUrl(n) {
  const url = new URL(window.location.href);
  if (manifestMode()) {
    url.searchParams.delete('page');
    url.searchParams.set('p', String(labelForIndex(n)));
  } else {
    url.searchParams.set('page', String(n));
  }
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
 * Заголовок вкладки — из metadata.json (title разбираемой книги).
 * Без метаданных остаётся статический заголовок из разметки.
 */
function setDocumentTitle() {
  if (metadata && metadata.title) {
    document.title = `${metadata.title} — Мединский.нет`;
  }
}

/**
 * Initialize the application
 */
async function init() {
  // ✅ ДОБАВИТЬ В НАЧАЛО init()
  currentDocId = getDocIdFromPath();
  console.log('Detected docId:', currentDocId);

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

  setDocumentTitle();

  // Add event listener for window resize
  window.addEventListener('resize', () => {
    isMobile = checkMobile();
    updateLayout();

    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      repositionAnnotations();
    }, 250);
  });

  // Determine initial index: ?p=<label> (manifest) -> legacy ?page=N ->
  // legacy #pageN -> default. See resolveStartIndex().
  const startPage = resolveStartIndex();

  // Manifest mode: normalize a legacy ?page=/#page URL to the new ?p=<label>
  // form without adding a history entry (old links still open the same
  // file, but the address bar moves to the canonical form going forward).
  if (manifestMode()) {
    const normalized = buildPageUrl(startPage);
    if (normalized !== window.location.pathname + window.location.search + window.location.hash) {
      history.replaceState({ page: startPage }, '', normalized);
    }
  }

  // Load the initial page
  loadPage(startPage);

  // Listen for Back/Forward navigation
  window.addEventListener('popstate', () => {
    loadPage(resolveStartIndex());
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
        const labelStr = u.searchParams.get('p');
        const pageStr = u.searchParams.get('page');
        if (manifestMode() && labelStr !== null) {
          e.preventDefault();
          const idx = indexForLabel(labelStr);
          if (idx !== -1) navigateTo(idx);
        } else if (pageStr) {
          e.preventDefault();
          navigateTo(resolveLegacyPageNumber(parseInt(pageStr, 10)));
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
 * Load a page for the given navigation index (1..N): the manifest index in
 * manifest mode, or the legacy logical page number otherwise -- both ranges
 * are identical (see clampPage), so callers don't need to know which mode
 * is active.
 * @param {number} pageNum - Navigation index to load
 */
async function loadPage(pageNum) {
  isMobile = checkMobile();
  closeMobileOverlay();

  document.querySelectorAll('.comment-popup').forEach(popup => popup.remove());
  document.querySelectorAll('.circle').forEach(circle => circle.remove());

  document.getElementById('image-container').style.display = 'flex';
  document.getElementById('global-comment-container').style.display = 'block';

  // ✅ УСТАНОВИТЬ currentPageNum перед вычислением
  currentPageNum = pageNum;

  if (manifestMode()) {
    currentPageId = fileForIndex(pageNum);
    currentPageLabel = String(labelForIndex(pageNum));
  } else {
    // Compute physical page: logicalStart + pageNum - 1
    const phys = (metadata.pageNumbering.physicalStart || 1) + (pageNum - 1);
    currentPageId = 'page_' + String(phys).padStart(3, '0');
    currentPageLabel = String(pageNum);
  }

  // ✅ ОБНОВИТЬ состояние редактора, если он присутствует
  if (window.RedPenEditor && window.RedPenEditor.state) {
    window.RedPenEditor.state.page.pageNum = currentPageNum;
    window.RedPenEditor.state.page.docId = currentDocId;
    // File key (e.g. "006", "-01"), independent of the logical page number
    // above -- see docs/page-addressing-proposal.md. pageNum is kept for
    // backwards compatibility; new code should use pageKey.
    window.RedPenEditor.state.page.pageKey = currentPageId.split('_')[1];
  }
  
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

  // Transitional: drafts used to live in a sibling page_<NNN>.drafts.json and
  // are now part of the file above. An offline copy can still hold both, so
  // merge the sidecar when it exists and drop ids we already have -- otherwise
  // every draft would render twice. Remove once no such copies are in use.
  if (draftsRequested()) {
    try {
      const drafts = await fetch('annotations/' + currentPageId + '.drafts.json')
        .then(r => (r.ok ? r.json() : []));
      if (Array.isArray(drafts) && drafts.length) {
        const seen = new Set(allAnns.map(a => a.id));
        allAnns = allAnns.concat(
          drafts.filter(a => !seen.has(a.id)).map(a => Object.assign({}, a, { draft: true }))
        );
      }
    } catch (e) { /* no drafts for this page */ }
  }

  allAnns = applyTagFilter(allAnns);

  const globalContainer = document.getElementById('global-comment-container');
  const globalDiv = document.getElementById('global-comment');
  const generalAnns = allAnns.filter(a => a.annType === 'general');
  const mainAnns = allAnns.filter(a => a.annType === 'main');

  globalContainer.style.display = 'block';
  globalContainer.style.border = '3px solid #DC143C';

  // In draft preview, tag draft entries so they're distinguishable from
  // published text in the shared global-comment block.
  const draftTag = a => (a.draft ? '<em class="draft-tag">[черновик]</em> ' : '');
  let globalContent = '';
  if (generalAnns.length) {
    globalContent += generalAnns.map(a => `<p class="${a.draft ? 'is-draft' : ''}">${draftTag(a)}${formatCommentText(a.text)}</p>`).join('');
  }
  if (mainAnns.length) {
    globalContent += mainAnns.map((a,i) => `<p class="${a.draft ? 'is-draft' : ''}"><strong>${i+1}.</strong> ${draftTag(a)}${formatCommentText(a.text)}</p>`).join('');
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
 * Jump to next page (by navigation index -- see loadPage())
 */
function nextPage() {
  const total = metadata.totalPages || 1;
  if (currentPageNum < total) navigateTo(currentPageNum + 1);
}

/**
 * Jump to previous page (by navigation index -- see loadPage())
 */
function prevPage() {
  if (currentPageNum > 1) navigateTo(currentPageNum - 1);
}

/**
 * Go to a page entered by the user: a manifest label (e.g. "A1", "17",
 * case-insensitive) in manifest mode, or a plain page number otherwise.
 */
function goToPage() {
  const input = document.getElementById('page-input');
  const totalPages = metadata.totalPages || 1;
  const idx = resolveIndexFromUserInput(input.value);

  if (!isNaN(idx)) {
    navigateTo(idx); // navigateTo() clamps
    input.value = ''; // Clear the input after navigation
  } else {
    alert(`Пожалуйста, введите номер страницы от 1 до ${totalPages}`);
  }
}

/**
 * Convert currentPageId (physical) to legacy logical page number. Legacy-mode
 * helper kept for callers outside main.js; in manifest mode use
 * currentPageNum (already the manifest index) instead.
 */
function logicalFromPhysical() {
  const phys = parseInt(currentPageId.split('_')[1], 10);
  return phys - (metadata.pageNumbering.physicalStart || 1) + 1;
}

/**
 * Update pagination based on the current navigation index and total count.
 * @param {number} currentPage - Current navigation index
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
    pageElement.textContent = manifestMode() ? labelForIndex(pageNum) : pageNum;
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
