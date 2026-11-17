"use client";

import { useState } from "react";
import type {
  ToolCallMessagePartComponent,
  ToolCallMessagePartStatus,
} from "@assistant-ui/react";
import { ToolCall } from "@/components/assistant-ui/elements/tool-call";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";

type SearchReportToolCallProps = {
  query?: string;
  argsText: string;
  result?: unknown;
  isError?: boolean;
  status: ToolCallMessagePartStatus;
};

function parseResult(result: unknown): unknown {
  if (typeof result !== "string") return result;
  try {
    return JSON.parse(result) as unknown;
  } catch {
    return result;
  }
}

function resultSummary(result: unknown): string {
  const parsed = parseResult(result);
  if (!Array.isArray(parsed)) {
    return typeof parsed === "string" && parsed ? parsed : "Search completed.";
  }

  if (parsed.length === 0) return "No relevant report excerpts found.";
  return `Found ${parsed.length.toLocaleString("en-US")} relevant ${parsed.length === 1 ? "excerpt" : "excerpts"}.`;
}

export function SearchReportToolCallDisclosure(props: SearchReportToolCallProps) {
  const [open, setOpen] = useState(false);
  const running = props.status.type === "running";
  const failed =
    props.isError === true ||
    (props.status.type === "incomplete" && props.status.reason === "error");
  const result =
    props.status.type === "incomplete"
      ? props.status.reason === "cancelled"
        ? "Search was cancelled."
        : String(props.status.error ?? "Search did not complete.")
      : props.isError
        ? typeof props.result === "string"
          ? props.result
          : JSON.stringify(props.result) ?? "Search failed."
        : resultSummary(props.result);

  return (
    <ToolCall
      label="Searched reports"
      activeLabel="Searching reports"
      query={props.query ?? ""}
      request={props.argsText}
      result={result}
      running={running}
      failed={failed}
      open={open}
      onOpenChange={setOpen}
    />
  );
}

export const SearchReportToolCall: ToolCallMessagePartComponent<
  { query?: string },
  unknown
> = (props) => {
  if (
    props.toolName !== "search_report_evidence" ||
    props.status.type === "requires-action" ||
    props.approval != null ||
    props.interrupt != null
  ) {
    return <ToolFallback {...props} />;
  }

  return (
    <SearchReportToolCallDisclosure
      query={typeof props.args?.query === "string" ? props.args.query : ""}
      argsText={props.argsText}
      result={props.result}
      isError={props.isError}
      status={props.status}
    />
  );
};
