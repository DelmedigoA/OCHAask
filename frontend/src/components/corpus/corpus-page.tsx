import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartContainer } from "@/components/ui/chart";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useIsMobile } from "@/hooks/use-mobile";
import { ReportTable, type CorpusReport } from "@/components/corpus/report-table";

type CorpusData = {
  reports_count: number;
  chunk_token_count: number;
  chunk_token_count_complete: boolean;
  date_start: string | null;
  date_end: string | null;
  monthly: { month: string; label: string; reports: number }[];
  reports: CorpusReport[];
};

async function fetchCorpus(signal?: AbortSignal) {
  const response = await fetch("/api/corpus", { signal });
  if (!response.ok) throw new Error("The indexed corpus is temporarily unavailable.");
  return response.json() as Promise<CorpusData>;
}

function dateLabel(value: string | null) {
  if (!value) return "—";
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function tokenLabel(value: number) {
  const millions = value / 1_000_000;
  return `${millions < 0.1 && value > 0 ? millions.toFixed(2) : millions.toFixed(1)}M`;
}

function CorpusStats({ data }: { data: CorpusData }) {
  const dateSpan = data.date_start || data.date_end
    ? `${dateLabel(data.date_start)} – ${dateLabel(data.date_end)}`
    : "—";
  const items = [
    { label: "Indexed reports", value: data.reports_count.toLocaleString("en-US") },
    { label: "Chunk tokens", value: tokenLabel(data.chunk_token_count) },
    { label: "Publication span", value: dateSpan },
  ];

  return (
    <dl className="grid grid-cols-1 divide-y border-y sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      {items.map((item) => (
        <div key={item.label} className="py-4 sm:px-5 first:sm:pl-0 last:sm:pr-0">
          <dt className="text-xs font-medium text-muted-foreground">{item.label}</dt>
          <dd className={item.label === "Publication span" ? "mt-1.5 text-lg font-semibold tracking-tight tabular-nums sm:text-xl" : "mt-1.5 text-xl font-semibold tracking-tight tabular-nums sm:text-2xl"}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function MonthlyChart({ monthly }: { monthly: CorpusData["monthly"] }) {
  const isMobile = useIsMobile();

  return (
    <section className="space-y-3" aria-labelledby="monthly-chart-title">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h2 id="monthly-chart-title" className="text-base font-semibold tracking-tight">Reports by month</h2>
          <p className="mt-1 text-sm text-muted-foreground">Monthly counts across the full publication span ({monthly.length} months).</p>
        </div>
      </div>
      {monthly.length ? (
        <ChartContainer
          config={{ reports: { label: "Reports", color: "var(--primary)" } }}
          className="h-44 min-h-0 w-full"
          role="img"
          aria-label="Monthly count of indexed reports, based on publication dates"
        >
          <BarChart accessibilityLayer data={monthly} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" interval={isMobile ? 5 : monthly.length > 24 ? 2 : 1} tick={{ fontSize: isMobile ? 10 : 12 }} tickLine={false} axisLine={false} tickMargin={8} />
            <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={32} />
            <Tooltip
              cursor={{ fill: "var(--muted)" }}
              contentStyle={{ backgroundColor: "var(--background)", borderColor: "var(--border)", borderRadius: "var(--radius)", color: "var(--foreground)" }}
              labelStyle={{ color: "var(--muted-foreground)" }}
              formatter={(value) => [Number(value).toLocaleString("en-US"), "Reports"]}
            />
            <Bar dataKey="reports" fill="var(--color-reports)" radius={[3, 3, 0, 0]} maxBarSize={22} />
          </BarChart>
        </ChartContainer>
      ) : (
        <div className="flex h-40 items-center justify-center border-y text-sm text-muted-foreground">No publication dates are available in the indexed corpus.</div>
      )}
    </section>
  );
}

export function CorpusPage() {
  const [data, setData] = useState<CorpusData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    void fetchCorpus(controller.signal)
      .then(setData)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "The indexed corpus is temporarily unavailable.");
        }
      });
    return () => controller.abort();
  }, [reloadKey]);

  return (
    <main className="mx-auto w-full max-w-6xl space-y-9 px-5 py-8 sm:px-8 sm:py-10">
      <header className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">OCHA report library</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Corpus</h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">An overview of the reports available for semantic search and research chat.</p>
      </header>

      {error ? (
        <div role="alert" className="flex flex-col items-start gap-3 border-y py-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">{error}</p>
          <Button variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>Retry</Button>
        </div>
      ) : data ? (
        <>
          <CorpusStats data={data} />
          <MonthlyChart monthly={data.monthly} />
          <p className="-mt-5 text-xs leading-5 text-muted-foreground">Token totals include chunk overlap; a deduplicated document-token total is not stored.</p>
          <ReportTable reports={data.reports} />
        </>
      ) : (
        <div className="space-y-8" aria-label="Loading corpus">
          <div className="grid gap-4 border-y py-5 sm:grid-cols-3">
            {[0, 1, 2].map((item) => <div key={item} className="space-y-2"><Skeleton className="h-3 w-24" /><Skeleton className="h-7 w-32" /><Skeleton className="h-3 w-48" /></div>)}
          </div>
          <Skeleton className="h-52 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
      )}
    </main>
  );
}
