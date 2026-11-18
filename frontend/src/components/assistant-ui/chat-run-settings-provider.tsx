import { type AssistantRuntime, useAui } from "@assistant-ui/react";
import { type PropsWithChildren, useEffect, useLayoutEffect, useState } from "react";
import {
  ChatRunSettingsContext,
  type ChatModel,
  type ReasoningEffort,
  type SourceSelection,
  priorTurnSources,
} from "@/lib/chat-run-settings";

export function ChatRunSettingsProvider({
  runtime,
  children,
}: PropsWithChildren<{ runtime: AssistantRuntime }>) {
  const aui = useAui();
  const [sources, setSources] = useState<SourceSelection>({ archive: true, ocha: true });
  const [effortOverride, setEffort] = useState<ReasoningEffort>();
  const [model, setModel] = useState<ChatModel>();
  const [configError, setConfigError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const effort = effortOverride ?? model?.defaultReasoningEffort ?? null;

  useEffect(() => {
    const controller = new AbortController();
    async function loadConfig() {
      const response = await fetch("/api/chat/config", { signal: controller.signal });
      if (!response.ok) throw new Error("Chat configuration failed to load");
      const config = await response.json() as { model: ChatModel };
      if (!controller.signal.aborted) setModel(config.model);
    }
    void loadConfig().catch(() => {
      if (!controller.signal.aborted) setConfigError(true);
    });
    return () => controller.abort();
  }, [attempt]);

  useLayoutEffect(() => aui.modelContext.register({
    getModelContext: () => {
      // AG-UI forwards the model context config keys as forwardedProps.
      const config = {
        modelName: model?.id,
        ochask: {
          modelId: model?.id,
          reasoningEffort: effort ?? undefined,
          sources,
          priorTurnSources: priorTurnSources(runtime.thread.getState().messages),
        },
      };
      return { config };
    },
  }), [aui, effort, model, runtime, sources]);

  return (
    <ChatRunSettingsContext.Provider value={{
      model,
      effort,
      sources,
      canSend: Boolean(model && (sources.archive || sources.ocha)),
      configError,
      toggleSource: (key) => setSources((current) => ({ ...current, [key]: !current[key] })),
      setEffort,
      retryConfig: () => {
        setConfigError(false);
        setAttempt((current) => current + 1);
      },
    }}>
      {children}
    </ChatRunSettingsContext.Provider>
  );
}
