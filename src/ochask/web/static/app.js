// Chat streaming and source citations for the ArchiveLens-style PDF workspace.
import { state, els } from "./state.js";
import { renderMarkdown, shortReportTitle } from "./formatting.js";
import {
  openDocumentViewer, closeDocumentViewer, changeDocumentPage, changeDocumentZoom,
  showDocumentPage, beginWorkspaceResize, handleDividerKeydown, renderDocumentPage,
} from "./document-viewer.js";

const sessionKey = "ochask:session";
const session = sessionStorage.getItem(sessionKey) || crypto.randomUUID();
sessionStorage.setItem(sessionKey, session);
const citationData = new WeakMap();
const historyKey = "ochask:messages";
let messages = [];
try { messages = JSON.parse(sessionStorage.getItem(historyKey) || "[]"); } catch { messages = []; }
if (!Array.isArray(messages)) messages = [];
function saveMessage(entry) {
  messages.push(entry);
  sessionStorage.setItem(historyKey, JSON.stringify(messages));
}

function message(role, text) {
  const node = document.createElement("article");
  node.className = `message message--${role}`;
  node.textContent = text;
  els.messages.append(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function renderAnswer(node, answer, citations) {
  const byMarker = new Map(citations.map(c => [c.marker, c]));
  const replacements = [];
  const prepared = answer.replace(/\[\[S\d+:p\d+\|[^\]\n]{1,500}\]\]/g, marker => {
    const index = replacements.push(byMarker.get(marker) || null) - 1;
    return `OCHASKCITATION${index}END`;
  });
  node.innerHTML = renderMarkdown(prepared);
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const textNode of nodes) {
    const text = textNode.nodeValue;
    const pattern = /OCHASKCITATION(\d+)END/g;
    let last = 0;
    const fragment = document.createDocumentFragment();
    for (const match of text.matchAll(pattern)) {
      fragment.append(document.createTextNode(text.slice(last, match.index)));
      const citation = replacements[Number(match[1])];
      if (citation) {
        const button = document.createElement("button");
        button.className = "document-citation-link";
        button.type = "button";
        button.textContent = `${shortReportTitle(citation.title)} · p. ${citation.page}`;
        button.title = `Open ${citation.title}, page ${citation.page}`;
        citationData.set(button, citation);
        fragment.append(button);
      } else {
        fragment.append(document.createTextNode("[unverified citation]"));
      }
      last = match.index + match[0].length;
    }
    if (last) { fragment.append(document.createTextNode(text.slice(last))); textNode.replaceWith(fragment); }
  }
}

async function ask(question) {
  state.busy = true;
  els.composer.querySelector("button").disabled = true;
  const node = message("assistant", "Searching reports…");
  let answer = "";
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session }),
    });
    if (!response.ok || !response.body) throw new Error("Chat request failed.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let complete = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const raw of events) {
        const name = raw.split("\n").find(line => line.startsWith("event: "))?.slice(7);
        const data = JSON.parse(raw.split("\n").find(line => line.startsWith("data: "))?.slice(6) || "{}");
        if (name === "delta") {
          answer += data.text;
          node.innerHTML = renderMarkdown(answer.replace(/\[\[S\d+:p\d+\|[^\]\n]*(?:\]\]|$)/g, ""));
        }
        if (name === "final") { renderAnswer(node, data.answer, data.citations); saveMessage({ role: "assistant", answer: data.answer, citations: data.citations }); complete = true; }
        if (name === "error") throw new Error(data.message);
      }
    }
    if (!complete) throw new Error("The response was interrupted. Please try again.");
  } catch (error) {
    node.textContent = error.message || "Chat failed.";
  } finally {
    state.busy = false;
    els.composer.querySelector("button").disabled = false;
    els.question.focus();
  }
}

els.composer.addEventListener("submit", event => {
  event.preventDefault();
  const question = els.question.value.trim();
  if (!question || state.busy) return;
  els.question.value = "";
  message("user", question);
  saveMessage({ role: "user", text: question });
  ask(question);
});
els.question.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); els.composer.requestSubmit(); }
});
els.resetConversation.addEventListener("click", async () => {
  if (state.busy) return;
  const response = await fetch("/api/chat/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session }) });
  if (response.ok) { messages = []; sessionStorage.removeItem(historyKey); els.messages.replaceChildren(); message("assistant", "Ask a question about the indexed reports."); }
});
document.addEventListener("click", event => {
  const citation = citationData.get(event.target.closest("button"));
  if (citation) openDocumentViewer(citation);
});
els.documentClose.addEventListener("click", closeDocumentViewer);
els.documentPrevious.addEventListener("click", () => changeDocumentPage(-1));
els.documentNext.addEventListener("click", () => changeDocumentPage(1));
els.documentZoomOut.addEventListener("click", () => changeDocumentZoom(-0.15));
els.documentZoomIn.addEventListener("click", () => changeDocumentZoom(0.15));
els.documentPage.addEventListener("change", () => showDocumentPage(Number(els.documentPage.value)));
els.divider.addEventListener("pointerdown", beginWorkspaceResize);
els.divider.addEventListener("keydown", handleDividerKeydown);
window.addEventListener("resize", () => { if (state.viewer.document) renderDocumentPage(); });
message("assistant", "Ask a question about the indexed reports.");
for (const entry of messages) {
  if (entry.role === "user") message("user", entry.text);
  else if (entry.role === "assistant") renderAnswer(message("assistant", ""), entry.answer, entry.citations || []);
}
