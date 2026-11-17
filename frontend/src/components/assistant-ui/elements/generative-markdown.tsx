"use client";

import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export type GenerativeMarkdownProps = {
  value?: string | undefined;
  children?: ReactNode;
};

export function GenerativeMarkdown({
  value,
  children,
}: GenerativeMarkdownProps) {
  return (
    <div data-aui="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a
              href={href}
              {...props}
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
        }}
      >
        {value ?? ""}
      </ReactMarkdown>
      {children}
    </div>
  );
}
