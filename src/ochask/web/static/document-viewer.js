// Lazy PDF loading, page/zoom controls, and the resizable workspace.
import { state, els } from "./state.js";
import { findEvidenceRanges } from "./evidence-highlights.js";

function setWorkspaceSplit(percent) {
  state.splitPercent = Math.max(30, Math.min(70, percent));
  els.workspace.style.setProperty("--chat-pane-width", `${state.splitPercent}%`);
  sessionStorage.setItem("archivelens:workspace-split", String(state.splitPercent));
}

function openWorkspace() {
  els.workspace.classList.add("workspace--viewer-open");
  els.documentPane.setAttribute("aria-hidden", "false");
  setWorkspaceSplit(state.splitPercent);
}

function closeDocumentViewer() {
  els.workspace.classList.remove("workspace--viewer-open");
  els.documentPane.setAttribute("aria-hidden", "true");
  els.question.focus();
}

function beginWorkspaceResize(event) {
  if (!els.workspace.classList.contains("workspace--viewer-open")) {
    return;
  }
  event.preventDefault();
  els.divider.setPointerCapture(event.pointerId);
  const resize = (moveEvent) => {
    const bounds = els.workspace.getBoundingClientRect();
    setWorkspaceSplit(((moveEvent.clientX - bounds.left) / bounds.width) * 100);
  };
  const finish = () => {
    els.divider.removeEventListener("pointermove", resize);
    els.divider.removeEventListener("pointerup", finish);
    els.divider.removeEventListener("pointercancel", finish);
    if (state.viewer.document) {
      renderDocumentPage();
    }
  };
  els.divider.addEventListener("pointermove", resize);
  els.divider.addEventListener("pointerup", finish);
  els.divider.addEventListener("pointercancel", finish);
}

function handleDividerKeydown(event) {
  if (!els.workspace.classList.contains("workspace--viewer-open")) {
    return;
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    setWorkspaceSplit(state.splitPercent + (event.key === "ArrowLeft" ? -5 : 5));
    if (state.viewer.document) {
      renderDocumentPage();
    }
  }
}

async function openDocumentViewer(citation) {
  openWorkspace();
  const requestId = (state.viewer.requestId || 0) + 1;
  state.viewer.requestId = requestId;
  state.viewer.evidence = citation.quote ? [citation.quote] : [];
  state.viewer.evidencePage = citation.page;
  els.documentTitle.textContent = citation.title;
  els.documentVersion.replaceChildren();
  if (citation.landing_url && /^https:\/\//i.test(citation.landing_url)) {
    const link = document.createElement("a");
    link.href = citation.landing_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Open original report ↗";
    els.documentVersion.append(link);
  }
  setDocumentStatus("Loading report…");
  try {
    const url = `/api/reports/${encodeURIComponent(citation.report_id)}/pdf`;
    if (!state.viewer.document || state.viewer.url !== url) {
      const pdfjs = await import("/static/vendor/pdfjs/pdf.mjs");
      pdfjs.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.mjs";
      const pdf = await pdfjs.getDocument({ url, rangeChunkSize: 65536 }).promise;
      if (state.viewer.requestId !== requestId) { await pdf.cleanup?.(); return; }
      if (state.viewer.document) await state.viewer.document.cleanup?.();
      state.viewer.pdfjs = pdfjs;
      state.viewer.document = pdf;
      state.viewer.url = url;
    }
    if (state.viewer.requestId === requestId) await showDocumentPage(citation.page);
  } catch (error) {
    if (state.viewer.requestId === requestId) setDocumentStatus(`The report PDF could not be loaded: ${error.message}`, true);
  }
}

async function showDocumentPage(page) {
  const pdf = state.viewer.document;
  if (!pdf) {
    return;
  }
  state.viewer.page = Math.max(1, Math.min(pdf.numPages, Math.round(page) || 1));
  els.documentPage.value = String(state.viewer.page);
  els.documentPage.max = String(pdf.numPages);
  els.documentPageCount.textContent = `of ${pdf.numPages}`;
  await renderDocumentPage();
}

async function changeDocumentPage(delta) {
  await showDocumentPage(state.viewer.page + delta);
}

async function changeDocumentZoom(delta) {
  state.viewer.zoom = Math.max(0.6, Math.min(2.5, state.viewer.zoom + delta));
  await renderDocumentPage();
}

async function renderDocumentPage() {
  const pdf = state.viewer.document;
  if (!pdf) {
    return;
  }
  setDocumentStatus(`Rendering page ${state.viewer.page}…`);
  const page = await pdf.getPage(state.viewer.page);
  const baseViewport = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(280, els.documentCanvasWrap.clientWidth - 28);
  const scale = Math.min(availableWidth / baseViewport.width, 2) * state.viewer.zoom;
  const viewport = page.getViewport({ scale });
  const textContent = await page.getTextContent();
  const pixelRatio = window.devicePixelRatio || 1;
  const context = els.documentCanvas.getContext("2d");
  els.documentCanvas.width = Math.floor(viewport.width * pixelRatio);
  els.documentCanvas.height = Math.floor(viewport.height * pixelRatio);
  els.documentCanvas.style.width = `${Math.floor(viewport.width)}px`;
  els.documentCanvas.style.height = `${Math.floor(viewport.height)}px`;
  const pageLayer = document.getElementById("document-page-layer");
  const textLayer = document.getElementById("document-text-layer");
  pageLayer.style.width = `${Math.floor(viewport.width)}px`;
  pageLayer.style.height = `${Math.floor(viewport.height)}px`;
  textLayer.replaceChildren();
  textLayer.style.setProperty("--total-scale-factor", scale);
  const textLayerTask = new state.viewer.pdfjs.TextLayer({
    textContentSource: textContent,
    container: textLayer,
    viewport,
  });
  await textLayerTask.render();
  await page.render({ canvasContext: context, viewport, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0] }).promise;
  const quotes = state.viewer.page === state.viewer.evidencePage ? state.viewer.evidence : [];
  const matched = applyEvidenceHighlights(quotes);
  let status = `Page ${state.viewer.page} of ${pdf.numPages}`;
  if (state.viewer.page === state.viewer.evidencePage) {
    status += !quotes.length ? " · This citation has no exact passage to highlight."
      : !matched ? " · The cited passage could not be matched on this page."
      : matched < quotes.length ? " · Some cited passages are highlighted; others could not be matched."
      : " · Cited passage highlighted.";
  }
  setDocumentStatus(status);
}

function applyEvidenceHighlights(quotes) {
  const textLayer = document.getElementById("document-text-layer");
  const spans = [...textLayer.querySelectorAll("span")].filter(span => !span.children.length);
  const { ranges, matchedQuotes } = findEvidenceRanges(spans.map(span => span.textContent), quotes);
  spans.forEach((span, index) => {
    if (ranges[index].length) wrapHighlightRanges(span, ranges[index]);
  });
  textLayer.querySelector("mark")?.scrollIntoView({ block: "center" });
  return matchedQuotes;
}

function wrapHighlightRanges(span, ranges) {
  const text = span.textContent;
  span.replaceChildren();
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) span.append(document.createTextNode(text.slice(cursor, start)));
    const mark = document.createElement("mark");
    mark.className = "textLayer__highlight";
    mark.textContent = text.slice(start, end);
    span.append(mark);
    cursor = end;
  }
  if (cursor < text.length) span.append(document.createTextNode(text.slice(cursor)));
}

function setDocumentStatus(message, isError = false) {
  els.documentStatus.textContent = message;
  els.documentStatus.classList.toggle("document-status--error", isError);
}

export { openDocumentViewer, closeDocumentViewer, changeDocumentPage, changeDocumentZoom, showDocumentPage, beginWorkspaceResize, handleDividerKeydown, renderDocumentPage };
