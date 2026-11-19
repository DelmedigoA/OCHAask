import type { ThreadMessage } from "@assistant-ui/react";
import { createContext, useContext } from "react";

export type SourceName = "archive" | "ocha";
export type SourceSelection = Record<SourceName, boolean>;
export type ReasoningEffort = "low" | "medium" | "high";

export const effortLabels: Record<ReasoningEffort, string> = {
  low: "Light",
  medium: "Medium",
  high: "High",
};

export type ChatModel = {
  id: string;
  label: string;
  reasoningEfforts: ReasoningEffort[];
  defaultReasoningEffort: ReasoningEffort | null;
};

export const DEFAULT_CHAT_MODEL: ChatModel = {
  id: "gpt-6-luna",
  label: "GPT-6 Luna",
  reasoningEfforts: ["low", "medium", "high"],
  defaultReasoningEffort: "low",
};

export type ChatRunSettings = {
  model: ChatModel | undefined;
  effort: ReasoningEffort | null;
  sources: SourceSelection;
  canSend: boolean;
  configError: boolean;
  toggleSource: (key: SourceName) => void;
  setEffort: (effort: ReasoningEffort) => void;
  retryConfig: () => void;
};

export const ChatRunSettingsContext = createContext<ChatRunSettings | null>(null);

export function useChatRunSettings() {
  const settings = useContext(ChatRunSettingsContext);
  if (!settings) throw new Error("Chat run settings are unavailable");
  return settings;
}

export function priorTurnSources(messages: readonly ThreadMessage[]) {
  const turns: Record<string, SourceName[]> = Object.create(null);
  let userMessageId: string | undefined;
  for (const message of messages) {
    if (message.role === "user") {
      userMessageId = message.id;
      continue;
    }
    if (message.role !== "assistant" || message.status.type !== "complete") continue;
    const state = message.metadata.unstable_state;
    if (state === null || typeof state !== "object" || !("ochaskSourceContext" in state)) continue;
    const context = state.ochaskSourceContext;
    if (
      !context || typeof context !== "object" ||
      !("userMessageId" in context) || typeof context.userMessageId !== "string" ||
      context.userMessageId !== userMessageId ||
      !("sources" in context) || !Array.isArray(context.sources) ||
      !context.sources.every((name) => name === "archive" || name === "ocha")
    ) continue;
    turns[userMessageId] = context.sources;
  }
  return turns;
}
