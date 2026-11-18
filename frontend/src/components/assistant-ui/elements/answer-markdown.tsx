"use client";

import {
  Children,
  createContext,
  isValidElement,
  type PropsWithChildren,
  type ReactNode,
  useContext,
  useMemo,
  useState,
} from "react";
import { type ThreadMessage, useAuiState } from "@assistant-ui/react";
import { buildAnswerEvidence } from "../answer-evidence";
import { Citation } from "./inline-citation";
import { MarkdownLink, type MarkdownLinkProps, MarkdownText } from "./markdown-text";
import { Sources } from "./sources";

const EvidenceContext = createContext<ReturnType<typeof buildAnswerEvidence> | null>(null);
const EMPTY_MESSAGES: readonly ThreadMessage[] = [];

function linkLabel(children: ReactNode): string {
  return Children.toArray(children).map((child) => {
    if (typeof child === "string" || typeof child === "number") return String(child);
    return isValidElement<{ children?: ReactNode }>(child)
      ? linkLabel(child.props.children)
      : "";
  }).join("");
}

const citationComponents = {
  a: function AnswerLink({ children, href, ...props }: MarkdownLinkProps) {
    const evidence = useContext(EvidenceContext);
    const citation = href ? evidence?.findCitation(href, linkLabel(children)) : undefined;
    if (citation) {
      return (
        <>
          {citation.retainLabel && children}
          <Citation index={citation.index} source={citation.source} />
        </>
      );
    }
    return <MarkdownLink href={href} {...props}>{children}</MarkdownLink>;
  },
};

export function AnswerMarkdown() {
  return <MarkdownText components={citationComponents} />;
}

export function AnswerEvidence({ children }: PropsWithChildren) {
  const messages = useAuiState((s) => s.message.content.some(
    (part) => part.type === "text" && part.text.includes("]"),
  ) ? s.thread.messages : EMPTY_MESSAGES);
  const index = useAuiState((s) => s.message.index);
  const answer = useMemo(() => buildAnswerEvidence(messages, index), [messages, index]);
  const [open, setOpen] = useState(false);

  return (
    <EvidenceContext.Provider value={answer}>
      {children}
      {answer.sources.length > 0 && (
        <Sources
          sources={answer.sources}
          open={open}
          onOpenChange={setOpen}
          layout="list"
          className="mt-4 max-w-none"
        />
      )}
    </EvidenceContext.Provider>
  );
}
