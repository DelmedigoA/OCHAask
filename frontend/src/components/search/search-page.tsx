import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ArrowUpRight, LoaderCircle, Search as SearchIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type SearchResult = {
  chunk_id: string;
  report_id: string;
  title: string;
  landing_url: string;
  pages: number[];
  content: string;
  fusion_score: number;
  publication_date: string | null;
};

type SearchStatus = "idle" | "loading" | "ready" | "error";

async function requestSearch(query: string, limit: number, signal: AbortSignal) {
  const response = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit }),
    signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: string } | null;
    throw new Error(payload?.error ?? "Search is temporarily unavailable.");
  }
  return response.json() as Promise<{ results: SearchResult[] }>;
}

function publicationDate(value: string | null) {
  return value ? new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : null;
}

function queryTerms(query: string) {
  return [...new Set(query.trim().split(/\s+/).filter((term) => term.length > 1))];
}

function excerpt(content: string, terms: string[]) {
  const lower = content.toLocaleLowerCase();
  const matchIndex = terms.map((term) => lower.indexOf(term.toLocaleLowerCase())).filter((index) => index >= 0).sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, matchIndex - 150);
  const end = Math.min(content.length, Math.max(matchIndex + 260, start + 360));
  return `${start > 0 ? "…" : ""}${content.slice(start, end).trim()}${end < content.length ? "…" : ""}`;
}

function highlight(content: string, terms: string[]): ReactNode[] {
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!escaped.length) return [content];
  const expression = new RegExp(`(${escaped.join("|")})`, "ig");
  return content.split(expression).filter(Boolean).map((part, index) => (
    terms.some((term) => term.toLocaleLowerCase() === part.toLocaleLowerCase())
      ? <mark key={index} className="rounded-sm bg-primary/10 px-0.5 text-inherit">{part}</mark>
      : <span key={index}>{part}</span>
  ));
}

function pagesLabel(pages: number[]) {
  const sorted = [...new Set(pages)].filter((page) => page > 0).sort((a, b) => a - b);
  if (!sorted.length) return "Page unavailable";
  const visible = sorted.slice(0, 4).map((page) => `p. ${page}`).join(", ");
  return `${visible}${sorted.length > 4 ? ` +${sorted.length - 4}` : ""}`;
}

function ResultItem({ result, terms }: { result: SearchResult; terms: string[] }) {
  const date = publicationDate(result.publication_date);
  return (
    <article className="space-y-2.5 border-b py-5 first:pt-0 last:border-b-0" aria-label={`Search result: ${result.title}`}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <a href={result.landing_url} target="_blank" rel="noreferrer" className="group max-w-4xl text-base font-semibold leading-6 tracking-tight hover:underline">
          {result.title}<ArrowUpRight className="ml-1.5 inline size-3.5 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
        </a>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground" title="Hybrid retrieval fusion score">{result.fusion_score.toFixed(4)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {date && <time dateTime={result.publication_date ?? undefined}>{date}</time>}
        <span>{pagesLabel(result.pages)}</span>
        <a href={result.landing_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground hover:underline">Open source<ArrowUpRight className="size-3" /></a>
      </div>
      <p className="max-w-4xl whitespace-pre-line text-sm leading-6 text-foreground/85">{highlight(excerpt(result.content, terms), terms)}</p>
    </article>
  );
}

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [status, setStatus] = useState<SearchStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  const runSearch = useCallback(async (value: string, count: number) => {
    const normalized = value.trim();
    if (!normalized) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setSubmittedQuery(normalized);
    setStatus("loading");
    setError(null);
    try {
      const response = await requestSearch(normalized, count, controller.signal);
      setResults(response.results);
      setStatus("ready");
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Search is temporarily unavailable.");
        setStatus("error");
      }
    }
  }, []);

  useEffect(() => () => requestRef.current?.abort(), []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runSearch(query, limit);
  }

  const terms = queryTerms(submittedQuery);

  return (
    <main className="mx-auto w-full max-w-4xl space-y-8 px-5 py-8 sm:px-8 sm:py-10">
      <header className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">OCHA report library</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Semantic search</h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">Find relevant evidence across the indexed OCHA reports.</p>
      </header>

      <form onSubmit={submit} className="space-y-3">
        <label htmlFor="report-search" className="sr-only">Search OCHA reports</label>
        <div className="flex flex-col gap-2 rounded-xl border bg-background p-2 shadow-sm sm:flex-row sm:items-center sm:gap-3 sm:rounded-2xl sm:p-3">
          <SearchIcon className="ml-2 hidden size-5 shrink-0 text-muted-foreground sm:block" aria-hidden="true" />
          <Input id="report-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ask about an issue, event, location, or response…" className="h-12 border-0 bg-transparent px-2 text-base shadow-none focus-visible:ring-0 sm:h-11" />
          <div className="flex shrink-0 items-center justify-between gap-2 border-t pt-2 sm:border-l sm:border-t-0 sm:pl-3 sm:pt-0">
            <label htmlFor="result-count" className="text-xs text-muted-foreground">Results</label>
            <select
              id="result-count"
              value={limit}
              onChange={(event) => {
                const nextLimit = Number(event.target.value);
                setLimit(nextLimit);
                if (submittedQuery) void runSearch(submittedQuery, nextLimit);
              }}
              className="h-8 rounded-md border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {[5, 10, 20, 30].map((count) => <option key={count} value={count}>{count}</option>)}
            </select>
            <Button type="submit" size="sm" className="ml-1 h-9 px-4" disabled={status === "loading" || !query.trim()}>
              {status === "loading" ? <LoaderCircle className="size-4 animate-spin" aria-label="Searching" /> : "Search"}
            </Button>
          </div>
        </div>
      </form>

      {status === "idle" && (
        <p className="border-t pt-5 text-sm text-muted-foreground">Search runs against the same hybrid retrieval used by Research Chat. Results keep the retrieval order.</p>
      )}
      {status === "loading" && (
        <div className="space-y-4" role="status" aria-live="polite">
          <p className="text-sm text-muted-foreground">Searching indexed reports…</p>
          {[0, 1, 2].map((item) => <div key={item} className="space-y-3 border-b pb-5"><div className="h-4 w-2/3 animate-pulse rounded bg-muted" /><div className="h-3 w-32 animate-pulse rounded bg-muted" /><div className="h-12 w-full animate-pulse rounded bg-muted/70" /></div>)}
        </div>
      )}
      {status === "error" && <p role="alert" className="border-y py-5 text-sm text-destructive">{error}</p>}
      {status === "ready" && (
        <section aria-live="polite" aria-label="Search results" className="space-y-5 border-t pt-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium">Results for <span className="font-semibold">“{submittedQuery}”</span></h2>
            <p className="text-xs text-muted-foreground">{results.length} {results.length === 1 ? "result" : "results"}</p>
          </div>
          {results.length ? (
            <div>{results.map((result) => <ResultItem key={result.chunk_id} result={result} terms={terms} />)}</div>
          ) : (
            <div className="py-10 text-center">
              <p className="text-sm font-medium">No matching passages found.</p>
              <p className="mt-1 text-sm text-muted-foreground">Try a broader phrase or another term from the reports.</p>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
