"use client";

import { useState } from "react";
import type {
  ToolCallMessagePartComponent,
  ToolCallMessagePartStatus,
} from "@assistant-ui/react";
import { ToolCall } from "@/components/assistant-ui/elements/tool-call";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";

type SearchReportToolCallProps = {
  toolName: string;
  argsText: string;
  result?: unknown;
  isError?: boolean;
  status: ToolCallMessagePartStatus;
};

const archiveToolPresentation: Record<
  string,
  { label: string; activeLabel: string }
> = {
  search_archive: {
    label: "Searched archive",
    activeLabel: "Searching archive",
  },
  lookup_url: {
    label: "Checked archive link",
    activeLabel: "Checking archive link",
  },
  request_archiving: {
    label: "Requested archiving",
    activeLabel: "Requesting archiving",
  },
};

function parseResult(result: unknown): unknown {
  if (typeof result !== "string") return result;
  try {
    return JSON.parse(result) as unknown;
  } catch {
    return result;
  }
}

function resultSummary(toolName: string, result: unknown): string {
  const parsed = parseResult(result);
  if (toolName === "search_archive") {
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const response = parsed as {
        cataloged?: { total?: unknown };
        unindexed?: { total?: unknown };
      };
      const cataloged = response.cataloged?.total;
      const unindexed = response.unindexed?.total;
      if (typeof cataloged === "number" || typeof unindexed === "number") {
        const count =
          (typeof cataloged === "number" ? cataloged : 0) +
          (typeof unindexed === "number" ? unindexed : 0);
        return `${count.toLocaleString("en-US")} matching ${count === 1 ? "item" : "items"} found.`;
      }
    }
    return "Archive search completed.";
  }

  if (toolName === "lookup_url") {
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const inArchive = (parsed as { in_archive?: unknown }).in_archive;
      if (typeof inArchive === "boolean") {
        return inArchive ? "Link found in the archive." : "Link not found in the archive.";
      }
    }
    return "Archive lookup completed.";
  }

  if (toolName === "request_archiving") {
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const answer = (parsed as { answer?: unknown }).answer;
      if (typeof answer === "string") {
        return `Request status: ${answer.replaceAll("_", " ")}.`;
      }
    }
    return "Archiving request submitted for review.";
  }

  if (!Array.isArray(parsed)) {
    return typeof parsed === "string" && parsed ? parsed : "Search completed.";
  }

  if (parsed.length === 0) return "No relevant report excerpts found.";
  return `Found ${parsed.length.toLocaleString("en-US")} relevant ${parsed.length === 1 ? "excerpt" : "excerpts"}.`;
}

export function SearchReportToolCallDisclosure(props: SearchReportToolCallProps) {
  const [open, setOpen] = useState(false);
  const archivePresentation = archiveToolPresentation[props.toolName];
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
        : resultSummary(props.toolName, props.result);

  return (
    <ToolCall
      label={archivePresentation?.label ?? "Searched reports"}
      activeLabel={archivePresentation?.activeLabel ?? "Searching reports"}
      request={props.argsText}
      result={result}
      running={running}
      failed={failed}
      open={open}
      onOpenChange={setOpen}
    />
  );
}

export const SearchReportToolCall: ToolCallMessagePartComponent = (props) => {
  const isArchiveTool = props.toolName in archiveToolPresentation;
  if (
    (props.toolName !== "search_report_evidence" && !isArchiveTool) ||
    props.status.type === "requires-action" ||
    props.approval != null ||
    props.interrupt != null
  ) {
    return <ToolFallback {...props} />;
  }

  return (
    <SearchReportToolCallDisclosure
      toolName={props.toolName}
      argsText={props.argsText}
      result={props.result}
      isError={props.isError}
      status={props.status}
    />
  );
};
