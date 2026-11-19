import type { ThreadMessage } from "@assistant-ui/react";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
import type { Source } from "./elements/inline-citation";

const parser = unified().use(remarkParse).use(remarkGfm).freeze();
const pagePattern = /(?:^|\s)p\.\s*(\d+)\s*$/i;

type Evidence = { key: string; source: Source; retainLabel?: boolean };

function absoluteUrl(value: unknown) {
  if (typeof value !== "string") return undefined;
  try {
    const { protocol } = new URL(value);
    if (protocol === "https:" || protocol === "http:") return value;
  } catch {
    return undefined;
  }
  return undefined;
}

function evidenceUrl(value: string) {
  try {
    const url = new URL(value);
    url.hash = "";
    for (const key of [...url.searchParams.keys()]) {
      if (/^(utm_.+|fbclid|gclid)$/i.test(key)) url.searchParams.delete(key);
    }
    url.searchParams.sort();
    url.pathname = url.pathname.replace(/\/+$/, "");
    return url.toString().replace(/\/$/, "");
  } catch {
    return value;
  }
}

function citationKey(url: string, page?: number) {
  return JSON.stringify([url, page]);
}

function sourceText(...values: unknown[]) {
  const value = values.find((candidate): candidate is string =>
    typeof candidate === "string" && Boolean(candidate.trim()),
  );
  return value?.trim();
}

function excerpt(...values: unknown[]) {
  const text = sourceText(...values)?.replace(/\s+/g, " ");
  return text && text.length > 300 ? `${text.slice(0, 297)}…` : text;
}

function parseResult(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const first = value.trimStart()[0];
  if (first !== "{" && first !== "[") return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

function recordOf(value: unknown): Record<string, unknown> | undefined {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return undefined;
}

function addOchaEvidence(value: unknown, matches: Map<string, Evidence>) {
  const pending: unknown[] = [value];
  for (let cursor = 0; cursor < pending.length; cursor++) {
    const parsed = parseResult(pending[cursor]);
    if (Array.isArray(parsed)) {
      pending.push(...parsed);
      continue;
    }
    const record = recordOf(parsed);
    if (!record) continue;

    const url = absoluteUrl(record.landing_url);
    const normalizedUrl = url ? evidenceUrl(url) : undefined;
    const title = sourceText(record.title);
    if (normalizedUrl && title && Array.isArray(record.pages)) {
      for (const rawPage of record.pages) {
        const page = typeof rawPage === "number"
          ? rawPage
          : typeof rawPage === "string" && /^\d+$/.test(rawPage)
            ? Number(rawPage)
            : undefined;
        if (page === undefined || !Number.isInteger(page) || page < 1) continue;
        const key = citationKey(normalizedUrl, page);
        if (matches.has(key)) continue;
        matches.set(key, {
          key,
          source: {
            title: `${title} · p. ${page}`,
            url,
            snippet: excerpt(record.content),
            publishedAt: sourceText(record.published_at, record.publication_date),
          },
        });
      }
    }

    for (const child of Object.values(record)) {
      if (child && typeof child === "object") pending.push(child);
      else if (typeof child === "string") pending.push(child);
    }
  }
}

function addArchiveEvidence(result: unknown, matches: Map<string, Evidence>) {
  // MCP results may arrive as structured content or as JSON in text blocks.
  const pending = [result];
  for (let i = 0; i < pending.length; i++) {
    const value = parseResult(pending[i]);
    if (Array.isArray(value)) {
      pending.push(...value);
      continue;
    }
    const record = recordOf(value);
    if (!record) continue;
    const original = absoluteUrl(record.url);
    const archived = absoluteUrl(record.page_url);
    const url = original ?? archived;
    if (url && record.type !== "resource_link") {
      const normalizedOriginal = original ? evidenceUrl(original) : undefined;
      const normalizedUrl = evidenceUrl(url);
      const title = sourceText(record.title, record.title_en, record.title_he, record.name) ?? url;
      const source = {
        title,
        snippet: excerpt(record.snippet, record.description, record.description_en, record.description_he, record.summary),
        publishedAt: sourceText(record.published_at, record.publication_date, record.date),
      };
      for (const link of new Set([original, archived])) {
        if (!link) continue;
        const linkKey = citationKey(evidenceUrl(link));
        if (matches.has(linkKey)) continue;
        matches.set(linkKey, {
          key: citationKey(normalizedOriginal ?? normalizedUrl),
          retainLabel: true,
          source: {
            ...source,
            title: original && link !== original ? `${title} (archived copy)` : title,
            url: link,
          },
        });
      }
    }
    const nested = Object.values(record).filter((child) => typeof child === "object");
    pending.push(...nested);
    if (record.type === "text") pending.push(record.text);
  }
}

function addBearingWitnessDocumentEvidence(value: unknown, matches: Map<string, Evidence>) {
  const pending: Array<{
    value: unknown;
    sourceUrl?: string;
    title?: string;
  }> = [{ value }];
  for (let cursor = 0; cursor < pending.length; cursor++) {
    const current = pending[cursor];
    const parsed = parseResult(current.value);
    if (Array.isArray(parsed)) {
      pending.push(...parsed.map((child) => ({
        value: child,
        sourceUrl: current.sourceUrl,
        title: current.title,
      })));
      continue;
    }
    const record = recordOf(parsed);
    if (!record) continue;
    const url = absoluteUrl(record.source_url) ?? absoluteUrl(current.sourceUrl);
    const normalizedUrl = url ? evidenceUrl(url) : undefined;
    const page = typeof record.page === "number" && Number.isInteger(record.page) && record.page > 0
      ? record.page
      : undefined;
    const title = sourceText(record.title, current.title);
    if (normalizedUrl && page !== undefined && title) {
      const key = citationKey(normalizedUrl, page);
      if (!matches.has(key)) {
        matches.set(key, {
          key,
          source: {
            title: `Bearing Witness · p. ${page}`,
            url,
            snippet: excerpt(record.excerpt, record.content, record.text),
          },
        });
      }
    }
    for (const child of Object.values(record)) {
      if (child && typeof child === "object") {
        pending.push({ value: child, sourceUrl: url, title });
      }
    }
  }
}

function addProjectContextEvidence(value: unknown, matches: Map<string, Evidence>) {
  const pending: unknown[] = [value];
  for (let cursor = 0; cursor < pending.length; cursor++) {
    const parsed = parseResult(pending[cursor]);
    if (Array.isArray(parsed)) {
      pending.push(...parsed);
      continue;
    }
    const record = recordOf(parsed);
    if (!record) continue;
    const url = absoluteUrl(record.source_url) ?? absoluteUrl(record.url);
    const title = sourceText(record.title, record.label, record.question, record.name);
    if (url && title) {
      const normalizedUrl = evidenceUrl(url);
      const key = citationKey(normalizedUrl);
      if (!matches.has(key)) {
        matches.set(key, {
          key,
          source: {
            title,
            url,
            snippet: excerpt(record.answer, record.description, record.summary,
              typeof record.content === "string" ? record.content : undefined),
          },
        });
      }
    }
    for (const child of Object.values(record)) {
      if (child && typeof child === "object") pending.push(child);
    }
  }
}

type MarkdownNode = {
  type: string;
  children?: readonly MarkdownNode[];
  value?: string;
  url?: string;
  identifier?: string;
};

function visit(node: MarkdownNode, apply: (node: MarkdownNode) => void) {
  apply(node);
  for (const child of node.children ?? []) visit(child, apply);
}

function labelText(node: MarkdownNode): string {
  return node.value ?? node.children?.map(labelText).join("") ?? "";
}

function numberFromLabel(label: string) {
  const page = pagePattern.exec(label)?.[1];
  return page ? Number(page) : undefined;
}

export function buildAnswerEvidence(messages: readonly ThreadMessage[], index: number) {
  const matched = new Map<string, Evidence>();
  const sources: Source[] = [];
  const numbered = new Map<string, number>();

  function findEvidence(href: string, label: string) {
    const page = numberFromLabel(label);
    const url = evidenceUrl(href);
    return (page !== undefined ? matched.get(citationKey(url, page)) : undefined)
      ?? matched.get(citationKey(url));
  }

  const answer = messages[index];
  if (answer?.role === "assistant") {
    const textParts = answer.content.flatMap((part) =>
      part.type === "text" && part.text.includes("]") ? [part.text] : [],
    );
    if (textParts.length) {
      for (let messageIndex = index; messageIndex >= 0; messageIndex--) {
        const message = messages[messageIndex];
        if (message?.role !== "assistant") continue;
        for (const part of message.content) {
          if (part.type !== "tool-call" || part.isError || part.result === undefined) continue;
          if (part.toolName === "search_ocha_corpus") addOchaEvidence(parseResult(part.result), matched);
          if (part.toolName === "search_archive" || part.toolName === "lookup_url") {
            addArchiveEvidence(part.result, matched);
          }
          if (part.toolName === "search_bearing_witness_document") {
            addBearingWitnessDocumentEvidence(part.result, matched);
          }
          if (part.toolName === "read_bearing_witness_pages") {
            addBearingWitnessDocumentEvidence(part.result, matched);
          }
          if (["search_faqs", "read_faq", "list_project_metadata_records", "read_project_metadata_record"].includes(part.toolName)) {
            addProjectContextEvidence(part.result, matched);
          }
        }
      }
      for (const text of textParts) {
        const tree = parser.parse(text);
        const definitions = new Map<string, string>();
        visit(tree, (node) => {
          if (node.type === "definition" && node.identifier && node.url && !definitions.has(node.identifier)) {
            definitions.set(node.identifier, node.url);
          }
        });
        visit(tree, (node) => {
          const href = node.type === "link" ? node.url
            : node.type === "linkReference" && node.identifier ? definitions.get(node.identifier)
            : undefined;
          if (!href) return;
          const evidence = findEvidence(href, labelText(node));
          if (!evidence || numbered.has(evidence.key)) return;
          numbered.set(evidence.key, sources.length);
          sources.push(evidence.source);
        });
      }
    }
  }

  return {
    sources,
    findCitation(href: string, label: string) {
      const evidence = findEvidence(href, label);
      if (!evidence) return undefined;
      const index = numbered.get(evidence.key);
      if (index === undefined) return undefined;
      return {
        index,
        source: evidence.source,
        retainLabel: evidence.retainLabel && label.trim() !== href,
      };
    },
  };
}
