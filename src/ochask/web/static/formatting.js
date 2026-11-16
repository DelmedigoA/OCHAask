// Parse Markdown locally; never interpret model-provided HTML or unsafe links.
import { Marked } from "./vendor/marked/marked.esm.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const markdown = new Marked({
  gfm: true,
  renderer: {
    html({ text }) { return escapeHtml(text); },
    image({ text }) { return escapeHtml(text); },
    link({ href, tokens }) {
      const label = this.parser.parseInline(tokens);
      let url;
      try { url = new URL(href); } catch { return label; }
      if (!["https:", "http:"].includes(url.protocol)) return label;
      return `<a href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    },
  },
});

function renderMarkdown(text) {
  return markdown.parse(String(text || ""));
}

function shortReportTitle(title) {
  const value = String(title || "Report");
  const situationUpdate = value.match(/Humanitarian Situation Update\s*#?\s*(\d+)/i);
  if (situationUpdate) return `Situation Update #${situationUpdate[1]}`;
  const flash = value.match(/Flash Update\s*#?\s*(\d+)/i);
  if (flash) return `Flash Update #${flash[1]}`;
  const report = value.match(/Situation Report\s*(?:No\.?|#)\s*(\d+)/i);
  if (report) return `Situation Report #${report[1]}`;
  return value.replace(/\s*\[[A-Z/ -]+\]\s*$/, "").trim();
}

export { renderMarkdown, shortReportTitle };
