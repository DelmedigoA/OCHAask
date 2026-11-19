import { useState } from "react";
import {
  createColumnHelper,
  useTable,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { dataTableFeatures } from "@/components/corpus/data-table-features";

export type CorpusReport = {
  id: string;
  title: string;
  publication_date: string | null;
  chunk_token_count: number;
  token_count_complete: boolean;
  landing_url: string | null;
  source_url: string | null;
};

const columnHelper = createColumnHelper<typeof dataTableFeatures, CorpusReport>();

function reportTitle(report: Pick<CorpusReport, "title">) {
  let title = report.title.replace(/^\s*oPt:\s*/i, "").trim();
  title = title.replace(/\s+\[(?:[A-Z]{2,3})(?:\/[A-Z]{2,3})*\]\s*$/i, "").trim();
  const dateSuffix = title.match(
    /\s*\|\s*(?:\d{1,2}\s+[A-Za-z]+\s*[-–]\s*)?\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s+[A-Za-z]+\s+\d{4}$/i,
  );
  if (dateSuffix) {
    title = title.slice(0, dateSuffix.index).trim();
  }

  return title || "Untitled report";
}

const sortableHeader = (title: string) => ({ column }: { column: { toggleSorting: (desc?: boolean) => void; getIsSorted: () => false | "asc" | "desc" } }) => (
  <Button
    variant="ghost"
    size="sm"
    className="-ml-2 h-7 px-2 text-xs font-medium text-muted-foreground"
    onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
  >
    {title}
    {column.getIsSorted() === "asc" ? <ArrowUp className="ml-1 size-3.5" /> : column.getIsSorted() === "desc" ? <ArrowDown className="ml-1 size-3.5" /> : <ArrowUpDown className="ml-1 size-3.5" />}
  </Button>
);

const columns = columnHelper.columns([
  columnHelper.accessor("title", {
    header: sortableHeader("Report title"),
    cell: ({ row }) => (
      <a
        href={row.original.landing_url ?? row.original.source_url ?? undefined}
        target="_blank"
        rel="noreferrer"
        className="line-clamp-2 min-w-64 max-w-[36rem] font-medium leading-5 text-foreground hover:underline"
      >
        {reportTitle(row.original)}
      </a>
    ),
  }),
  columnHelper.accessor("publication_date", {
    header: sortableHeader("Publication date"),
    cell: ({ getValue }) => getValue() ? new Date(`${getValue()}T00:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : <span className="text-muted-foreground">—</span>,
  }),
  columnHelper.accessor("chunk_token_count", {
    header: sortableHeader("Tokens"),
    cell: ({ row }) => <span className="tabular-nums">{row.original.token_count_complete ? row.original.chunk_token_count.toLocaleString("en-US") : `≥${row.original.chunk_token_count.toLocaleString("en-US")}`}</span>,
  }),
]);

export function ReportTable({ reports }: { reports: CorpusReport[] }) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "publication_date", desc: true },
  ]);
  const table = useTable({
    features: dataTableFeatures,
    data: reports,
    columns,
    initialState: { pagination: { pageIndex: 0, pageSize: 20 } },
    onSortingChange: setSorting,
    state: { sorting },
  });
  const pageCount = Math.max(1, table.getPageCount());

  return (
    <section className="space-y-4" aria-labelledby="report-catalog-title">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="report-catalog-title" className="text-base font-semibold tracking-tight">Indexed reports</h2>
          <p className="mt-1 text-sm text-muted-foreground">Browse and sort the indexed OCHA reports.</p>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border bg-background">
        <Table className="min-w-[680px]">
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id} className="bg-muted/30 hover:bg-muted/30">
                {group.headers.map((header) => (
                  <TableHead key={header.id}>
                    {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length ? table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => <TableCell key={cell.id}><table.FlexRender cell={cell} /></TableCell>)}
              </TableRow>
            )) : (
              <TableRow><TableCell colSpan={columns.length} className="h-24 text-center text-sm text-muted-foreground">No indexed reports are available.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col gap-3 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <p>{reports.length.toLocaleString("en-US")} {reports.length === 1 ? "report" : "reports"}</p>
        <div className="flex items-center gap-2 self-end sm:self-auto">
          <span className="mr-2 tabular-nums">Page {table.state.pagination.pageIndex + 1} of {pageCount}</span>
          <Button variant="outline" size="icon" className="size-8" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} aria-label="Previous page"><ChevronLeft className="size-4" /></Button>
          <Button variant="outline" size="icon" className="size-8" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} aria-label="Next page"><ChevronRight className="size-4" /></Button>
        </div>
      </div>
    </section>
  );
}
