"use client";

import {
  UserMessageAttachments,
} from "@/components/assistant-ui/elements/attachment.aui";
import { AnswerEvidence, AnswerMarkdown } from "@/components/assistant-ui/elements/answer-markdown";
import { ChatComposerControls } from "@/components/assistant-ui/elements/chat-composer-controls";
import { ComposerActions, ComposerBar, ComposerToolbar } from "@/components/assistant-ui/elements/composer";
import { File, getFileHref } from "@/components/assistant-ui/elements/file";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/elements/follow-up-suggestions.aui";
import { GeoMapToolCall } from "@/components/assistant-ui/elements/geo-map-tool-call";
import { GuardrailNotice } from "@/components/assistant-ui/elements/guardrail-notice";
import { Image } from "@/components/assistant-ui/elements/image";
import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/elements/reasoning.aui";
import { ThinkingIndicator } from "@/components/assistant-ui/elements/thinking-indicator";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useChatRunSettings } from "@/lib/chat-run-settings";
import {
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  BranchPickerPrimitive,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type FileMessagePartComponent,
  type ImageMessagePartComponent,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  AudioLinesIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CopyIcon,
  MicIcon,
  PencilIcon,
  PhoneIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  Loader2Icon,
  SquareIcon,
  PlayIcon,
} from "lucide-react";
import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentType,
  type FC,
  type PropsWithChildren,
} from "react";

export type ThreadGroupPart = MessagePrimitive.GroupedParts.GroupPart;

const shouldShowComposerResume = (s: AssistantState) =>
  !!s.thread.canResume && s.composer.type === "thread" && s.composer.isEmpty;

const reasoningDuration = (timing: ThreadGroupPart["timing"]) =>
  timing?.completedAt === undefined
    ? undefined
    : Math.round((timing.completedAt - timing.startedAt) / 1000);

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; the remaining slots override how the
 * assistant message renders tool calls and part groups. Tool UIs registered
 * by name (toolkit `render`, `useAssistantDataUI`) take precedence over
 * `ToolFallback`. When `TaskGroup` is set, tool calls that carry a nested
 * conversation and have no registered UI render through it instead of the
 * tool group; without it they render like any other tool call.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
  ToolGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
  ReasoningGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
  TaskGroup?: ComponentType<{ group: ThreadGroupPart }> | undefined;
};

const defaultMessageGroupBy = groupPartByType({
  reasoning: ["group-chainOfThought", "group-reasoning"],
  "tool-call": ["group-chainOfThought", "group-tool"],
  "standalone-tool-call": [],
});

type MessagePart = Parameters<typeof defaultMessageGroupBy>[0];
type ToolPart = Extract<MessagePart, { type: "tool-call" }>;

const isMapTool = (name: string) => name === "mapping" || name === "show_geo_map";
const MAPPING_PROGRESS = "Mapping...";

const needsToolInput = (part: ToolPart) =>
  part.status.type === "requires-action" || part.approval != null || part.interrupt != null;

function isHiddenToolCall(
  part: MessagePart,
  toolUIs: AssistantState["tools"]["toolUIs"] | undefined,
  showTasks = false,
) {
  if (part.type !== "tool-call") return false;
  if (part.toolName === "search_location") return true;
  return !needsToolInput(part) &&
    (!isMapTool(part.toolName) || part.status.type === "running") &&
    !(showTasks && part.messages !== undefined) &&
    !part.mcp?.app?.resourceUri &&
    !toolUIs?.[part.toolName]?.length;
}

const toolProgressLabels = new Map([
  ["search_ocha_corpus", "Searching OCHA reports"],
  ["search_bearing_witness_document", "Searching the Bearing Witness document"],
  ["read_bearing_witness_pages", "Reading Bearing Witness pages"],
  ["search_faqs", "Searching Bearing Witness FAQs"],
  ["read_faq", "Reading a Bearing Witness FAQ"],
  ["list_project_metadata_records", "Checking Bearing Witness project information"],
  ["read_project_metadata_record", "Reading Bearing Witness project information"],
  ["search_archive", "Searching the archive"],
  ["lookup_url", "Checking the archive"],
  ["request_archiving", "Requesting archiving"],
  ["mapping", MAPPING_PROGRESS],
  ["show_geo_map", "Preparing interactive map"],
]);

const toolProgressLabel = (toolName: string) => {
  const normalizedName = toolName.toLowerCase();
  for (const [name, label] of toolProgressLabels) {
    if (normalizedName === name || normalizedName.endsWith(`_${name}`)) {
      return label;
    }
  }
  return undefined;
};

function thinkingLabel(s: AssistantState) {
  if (!s.thread.isRunning) return undefined;
  let hasPendingTool = false;
  for (const part of s.message.parts) {
    if (part.type !== "tool-call" || part.status.type !== "running") continue;
    hasPendingTool = true;
    const label = toolProgressLabel(part.toolName);
    if (label) return label;
  }
  if (s.message.status?.type !== "running") return undefined;
  const hasText = s.message.parts.some((part) => part.type === "text" && part.text.trim());
  return hasPendingTool || !hasText ? "Thinking" : undefined;
}

const MIN_PROGRESS_LABEL_MS = 700;

function useVisibleThinkingLabel(label: string | undefined) {
  const [visibleLabel, setVisibleLabel] = useState(label);
  const lastProgress = useRef<{ label: string; startedAt: number } | undefined>(undefined);

  useEffect(() => {
    if (label && label !== "Thinking") {
      if (lastProgress.current?.label !== label) {
        lastProgress.current = { label, startedAt: Date.now() };
      }
      setVisibleLabel(label);
      return;
    }

    const previous = lastProgress.current;
    if (previous && previous.label !== MAPPING_PROGRESS) {
      const remaining = MIN_PROGRESS_LABEL_MS - (Date.now() - previous.startedAt);
      if (remaining > 0) {
        setVisibleLabel(previous.label);
        const timeout = window.setTimeout(() => {
          lastProgress.current = undefined;
          setVisibleLabel(label);
        }, remaining);
        return () => window.clearTimeout(timeout);
      }
    }

    lastProgress.current = undefined;
    setVisibleLabel(label);
  }, [label]);

  return visibleLabel;
}

const messageGroupBy: typeof defaultMessageGroupBy = (part, context) =>
  isHiddenToolCall(part, context?.toolUIs) ? [] : defaultMessageGroupBy(part, context);

type ThreadGroupKey =
  | "group-chainOfThought"
  | "group-reasoning"
  | "group-tool"
  | "group-task";

const TASK_GROUP_PATH: readonly ThreadGroupKey[] = [
  "group-chainOfThought",
  "group-task",
];

const taskAwareGroupBy = (
  part: Parameters<typeof messageGroupBy>[0],
  context?: Parameters<typeof messageGroupBy>[1],
): readonly ThreadGroupKey[] => {
  if (isHiddenToolCall(part, context?.toolUIs, true)) return [];
  const path = defaultMessageGroupBy(part, context);
  return part.type === "tool-call" &&
    part.messages !== undefined &&
    path.length > 0 &&
    !context?.toolUIs?.[part.toolName]?.length
    ? TASK_GROUP_PATH
    : path;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
  autoFocus?: boolean | undefined;
};

type ThreadRootProps = {
  isEmpty: boolean;
  autoFocus: boolean;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

// A switched thread that is still fetching its history: skeleton, not welcome.
const isHistoryLoadingView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  s.thread.isLoading &&
  !s.thread.isDisabled &&
  !s.threads.isLoading;

const ThreadHistorySkeleton: FC = () => (
  <div
    data-slot="aui_thread-history-skeleton"
    role="status"
    className="animate-in fade-in fill-mode-both flex flex-col gap-y-6 [animation-delay:150ms] [animation-duration:200ms]"
  >
    <span className="sr-only">Loading conversation</span>
    <Skeleton className="ml-auto h-9 w-2/5 rounded-xl motion-reduce:animate-none" />
    <div className="flex flex-col gap-y-2">
      <Skeleton className="h-4 w-11/12 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-4/5 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-3/5 motion-reduce:animate-none" />
    </div>
    <Skeleton className="ml-auto h-9 w-1/3 rounded-xl motion-reduce:animate-none" />
    <div className="flex flex-col gap-y-2">
      <Skeleton className="h-4 w-10/12 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-2/3 motion-reduce:animate-none" />
    </div>
  </div>
);

export const Thread: FC<ThreadProps> = ({
  components = EMPTY_COMPONENTS,
  autoFocus = true,
}) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot isEmpty={isEmpty} autoFocus={autoFocus} />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<ThreadRootProps> = ({
  isEmpty,
  autoFocus,
}) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);
  const { canSend } = useChatRunSettings();

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-background @container flex h-full min-w-0 w-full flex-1 flex-col"
      style={{
        ["--thread-max-width" as string]: "47.3rem",
        ["--composer-bg" as string]:
          "var(--color-background)",
        ["--composer-radius" as string]: "1rem",
        ["--composer-padding" as string]: "0.5rem",
      }}
    >
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-slot="aui_thread-viewport"
          className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll scroll-smooth"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 pt-8",
            isEmpty && "justify-center",
          )}
        >
          <AuiIf condition={isNewChatView}>
            <Welcome />
          </AuiIf>
          <AuiIf condition={isHistoryLoadingView}>
            <ThreadHistorySkeleton />
          </AuiIf>

          <ThreadLoadEarlier />

          <div
            data-slot="aui_message-group"
            className="mb-14 flex flex-col gap-y-8 empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer bg-background flex flex-col gap-4 overflow-visible pb-4 md:pb-6",
              !isEmpty &&
                "sticky bottom-0 mt-auto rounded-t-(--composer-radius)",
            )}
          >
            <ThreadScrollToBottom />
            {canSend && <ThreadFollowupSuggestions />}
            <Composer autoFocus={autoFocus} />
            <AuiIf condition={(s) => canSend && isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const { AssistantMessage: AssistantMessageComponent = AssistantMessage } =
    useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);
  const isEditing = useAuiState((s) => s.message.composer.isEditing);
  const isSpoken = useAuiState((s) => s.message.metadata.modality === "voice");

  if (isEditing) return <EditComposer />;
  if (isSpoken) return <SpokenMessage />;
  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
};

type VoiceRunPosition = "single" | "start" | "middle" | "end";

const useVoiceRunPosition = (): VoiceRunPosition =>
  useAuiState((s) => {
    const before =
      s.thread.messages[s.message.index - 1]?.metadata.modality === "voice";
    const after =
      s.thread.messages[s.message.index + 1]?.metadata.modality === "voice";
    if (before) return after ? "middle" : "end";
    return after ? "start" : "single";
  });

const SpokenText: TextMessagePartComponent = ({ text }) => (
  <p className="aui-spoken-message-text m-0">{text}</p>
);

const SpokenMessage: FC = () => {
  const role = useAuiState((s) => s.message.role);
  const position = useVoiceRunPosition();
  const isSpeaking = useAuiState(
    (s) =>
      s.message.role === "assistant" && s.message.status?.type === "running",
  );
  const opensExchange = position === "start" || position === "single";

  return (
    <MessagePrimitive.Root
      data-slot="aui_spoken-message-root"
      data-role={role}
      data-voice-run={position}
      className={cn(
        "aui-spoken-message bg-muted/40 mx-2 px-3 py-1.5 [contain-intrinsic-size:auto_48px] [content-visibility:auto]",
        position === "single" && "rounded-xl py-2",
        position === "start" && "rounded-t-xl pt-2",
        position === "middle" && "-mt-6",
        position === "end" && "-mt-6 rounded-b-xl pb-2",
      )}
    >
      {opensExchange && (
        <div
          data-slot="aui_spoken-exchange-header"
          className="text-muted-foreground mb-1.5 flex items-center gap-1.5 text-xs"
        >
          <PhoneIcon className="size-3" aria-hidden />
          <span>Voice conversation</span>
        </div>
      )}
      <div
        data-slot="aui_spoken-message-content"
        className="text-foreground flex items-start gap-2 text-sm leading-relaxed"
      >
        <span className="text-muted-foreground mt-1 shrink-0" aria-hidden>
          {role === "user" ? (
            <MicIcon className="size-3.5" />
          ) : (
            <AudioLinesIcon className="size-3.5" />
          )}
        </span>
        <span className="sr-only">
          {role === "user" ? "You said" : "Assistant said"}
        </span>
        <div className="min-w-0 flex-1 wrap-break-word">
          <MessagePrimitive.Parts components={{ Text: SpokenText }} />
          {isSpeaking && (
            <span
              data-slot="aui_spoken-message-indicator"
              role="status"
              className="text-muted-foreground ms-1 animate-pulse font-sans"
              aria-label="Assistant is speaking"
            >
              ●
            </span>
          )}
        </div>
      </div>
      <ActionBarPrimitive.Root hideWhenRunning autohide="not-last" className="mt-1">
        <CopyMessage />
      </ActionBarPrimitive.Root>
    </MessagePrimitive.Root>
  );
};

const FOCUSABLE_SELECTOR =
  "a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1']):not([disabled])";

const nextFocusable = (start: Element) => {
  for (
    let element = start.nextElementSibling;
    element;
    element = element.nextElementSibling
  ) {
    const target = element.matches(FOCUSABLE_SELECTOR)
      ? element
      : element.querySelector(FOCUSABLE_SELECTOR);
    if (target instanceof HTMLElement) return target;
  }
  return null;
};

const ThreadLoadEarlier: FC = () => {
  const visible = useAuiState(
    (s) => s.thread.hasEarlier || s.thread.isLoadingEarlier,
  );
  const loading = useAuiState((s) => s.thread.isLoadingEarlier);
  const slotRef = useRef<HTMLDivElement>(null);
  const focusedRef = useRef<Element | null>(null);

  // The last page removes the button; a keyboard user on it continues from the
  // next control in tab order rather than from the document body.
  useLayoutEffect(() => {
    const focused = focusedRef.current;
    const slot = slotRef.current;
    if (visible || !focused || focused.isConnected || !slot) return;
    focusedRef.current = null;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    nextFocusable(slot)?.focus();
  }, [visible]);

  return (
    <div
      ref={slotRef}
      className="contents"
      onFocus={(event) => {
        focusedRef.current = event.target;
      }}
    >
      <span role="status" className="sr-only">
        {loading ? "Loading earlier messages" : ""}
      </span>
      {visible && (
        <ThreadPrimitive.LoadEarlier render={<Button variant="ghost" size="sm" data-slot="aui_thread-load-earlier" className="aui-thread-load-earlier text-muted-foreground mb-6 self-center rounded-full" />}><span
                            className={cn(loading && "shimmer motion-reduce:animate-none")}
                          >
                            {loading ? "Loading earlier messages" : "Load earlier messages"}
                          </span></ThreadPrimitive.LoadEarlier>
      )}
    </div>
  );
};

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom render={<TooltipIconButton tooltip="Scroll to bottom" variant="outline" className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent absolute -top-12 z-10 self-center rounded-full p-4 disabled:invisible" />}><ArrowDownIcon /></ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-6 flex w-full flex-col items-center px-2 text-center">
      <p className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-4xl font-semibold tracking-[-0.05em] duration-200 sm:text-5xl">
        Bearing Witness Research Assistant
      </p>
      <p className="aui-thread-welcome-message-inner mx-auto mt-3 w-full max-w-md text-center text-sm leading-6 text-muted-foreground sm:text-base">
        Ask about the Bearing Witness project, its data, OCHA reports, and the archive.
      </p>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-col">
      <ThreadPrimitive.Suggestions>
        {() => <ThreadSuggestionItem />}
      </ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send render={<button type="button" className="aui-thread-welcome-suggestion group hover:bg-foreground/[0.03] focus-visible:ring-ring/50 flex w-full items-baseline gap-2.5 rounded-md px-2 py-2 text-start text-sm transition-colors outline-none focus-visible:ring-1 motion-reduce:transition-none" />}><span
                      aria-hidden
                      className="text-muted-foreground/60 group-hover:text-foreground font-mono text-xs transition-colors motion-reduce:transition-none"
                    >
                      {">"}
                    </span><span className="min-w-0 flex-1 truncate">
                      <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1 text-foreground" />{" "}
                      <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 text-muted-foreground empty:hidden" />
                    </span></SuggestionPrimitive.Trigger>
    </div>
  );
};

type ComposerProps = Pick<ThreadRootProps, "autoFocus">;

const Composer: FC<ComposerProps> = ({ autoFocus }) => {
  const { canSend, sources, configError, retryConfig } = useChatRunSettings();
  const noSources = !sources.archive && !sources.ocha;

  return (
    <ComposerPrimitive.Root
      data-slot="composer"
      className="aui-composer-root relative w-full"
      onSubmit={(event) => { if (!canSend) event.preventDefault(); }}
    >
      <ComposerBar className="gap-1 rounded-2xl border border-input bg-background p-2 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20">
        <ComposerPrimitive.Input
          placeholder="Ask about OCHA reports or Bearing Witness…"
          className="aui-composer-input max-h-48 min-h-20 w-full resize-none bg-transparent px-3 pt-2 text-base leading-6 outline-none placeholder:text-muted-foreground/60"
          rows={2}
          autoFocus={autoFocus}
          submitMode={canSend ? "enter" : "none"}
          enterKeyHint={canSend ? "send" : undefined}
          aria-label="Message input"
          aria-describedby={noSources ? "ochask-composer-source-help" : undefined}
        />
        <ComposerAction />
        {noSources && (
          <p id="ochask-composer-source-help" className="px-2 text-xs text-muted-foreground" role="status">
            Enable Archive or OCHA to send.
          </p>
        )}
        {configError ? (
          <div className="flex flex-wrap items-center gap-2 px-2 text-xs text-muted-foreground">
            <span role="status">Chat settings could not load.</span>
            <Button type="button" size="xs" variant="ghost" onClick={retryConfig}>Retry</Button>
          </div>
        ) : null}
      </ComposerBar>
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  const { canSend } = useChatRunSettings();
  // The stop control only cancels the send while no run it could stop is going.
  const isSending = useAuiState(
    (s) =>
      s.composer.submission !== undefined &&
      !(s.thread.isRunning && s.thread.capabilities.cancel),
  );

  return (
    <ComposerToolbar className="aui-composer-action-wrapper relative mt-3 gap-2 px-1 pb-1">
      <ChatComposerControls />
      <ComposerActions className="ms-auto shrink-0 self-end">
        <AuiIf condition={(s) => s.thread.capabilities.dictation}>
          <AuiIf condition={(s) => s.composer.dictation == null}>
            <ComposerPrimitive.Dictate render={<TooltipIconButton tooltip="Voice input" side="bottom" type="button" variant="ghost" size="icon" className="aui-composer-dictate text-muted-foreground hover:text-foreground size-7 rounded-full" aria-label="Start voice input" />}><MicIcon className="aui-composer-dictate-icon size-4" /></ComposerPrimitive.Dictate>
          </AuiIf>
          <AuiIf condition={(s) => s.composer.dictation != null}>
            <ComposerPrimitive.StopDictation render={<TooltipIconButton tooltip="Stop dictation" side="bottom" type="button" variant="ghost" size="icon" className="aui-composer-stop-dictation text-destructive size-7 rounded-full" aria-label="Stop voice input" />}><SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" /></ComposerPrimitive.StopDictation>
          </AuiIf>
        </AuiIf>
        <AuiIf
          condition={(s) =>
            (!s.composer.canCancel ||
              (s.thread.voice !== undefined &&
                s.composer.submission === undefined)) &&
            !shouldShowComposerResume(s)
          }
        >
          <ComposerPrimitive.Send disabled={!canSend} render={<TooltipIconButton tooltip="Send message" side="bottom" type="button" variant="default" size="icon" className="aui-composer-send size-7 rounded-full" aria-label="Send message" />}><ArrowUpIcon className="aui-composer-send-icon size-4" /></ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={shouldShowComposerResume}>
          <ComposerPrimitive.Resume disabled={!canSend} render={<TooltipIconButton tooltip="Resume generating" side="bottom" type="button" variant="default" size="icon" className="aui-composer-resume size-7 rounded-full" aria-label="Resume generating" />}><PlayIcon className="aui-composer-resume-icon size-4" /></ComposerPrimitive.Resume>
        </AuiIf>
        <AuiIf
          condition={(s) =>
            s.composer.canCancel &&
            (s.thread.voice === undefined ||
              s.composer.submission !== undefined)
          }
        >
          <ComposerPrimitive.Cancel render={<Button type="button" variant="default" size="icon" className="aui-composer-cancel size-7 rounded-full" aria-label={isSending ? "Cancel sending" : "Stop generating"} />}><SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" /></ComposerPrimitive.Cancel>
        </AuiIf>
      </ComposerActions>
    </ComposerToolbar>
  );
};

const MessageError: FC = () => {
  const { canSend } = useChatRunSettings();
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root group/error border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 data-[run-state=in-progress]:border-border data-[run-state=in-progress]:bg-muted/40 data-[run-state=in-progress]:text-muted-foreground mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border p-3 text-sm data-[run-state=stale]:border-amber-500/40 data-[run-state=stale]:bg-amber-500/10 data-[run-state=stale]:text-amber-800 dark:text-red-200 dark:data-[run-state=stale]:bg-amber-500/5 dark:data-[run-state=stale]:text-amber-200">
        <TriangleAlertIcon className="hidden size-4 shrink-0 group-data-[run-state=stale]/error:block" />
        <Loader2Icon className="hidden size-4 shrink-0 animate-spin group-data-[run-state=in-progress]/error:block motion-reduce:animate-none" />
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2 min-w-0 flex-1" />
        <ActionBarPrimitive.Resume disabled={!canSend} render={<Button variant="outline" size="sm" className="h-7 gap-1.5 border-amber-500/40 bg-transparent hover:bg-amber-500/15 disabled:hidden dark:border-amber-400/30" />}><PlayIcon className="size-3.5" />Resume
                        </ActionBarPrimitive.Resume>
        <ActionBarPrimitive.RefreshRun render={<Button variant="ghost" size="sm" className="h-7 gap-1.5 disabled:hidden" />}><RefreshCwIcon className="size-3.5" />Refresh
                        </ActionBarPrimitive.RefreshRun>
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC = () => {
  const progressLabel = useVisibleThinkingLabel(useAuiState(thinkingLabel));
  const {
    ToolFallback: ToolFallbackComponent = ToolFallback,
    ToolGroup,
    ReasoningGroup,
    TaskGroup: TaskGroupComponent,
  } = useContext(ThreadComponentsContext);
  const groupBy = TaskGroupComponent ? taskAwareGroupBy : messageGroupBy;
  const toolUIs = useAuiState((s) => s.tools.toolUIs);
  const isScopeRefusal = useAuiState((s) => {
    const state = s.message.metadata.unstable_state;
    return state !== null && typeof state === "object" &&
      "ochaskGuardrailMessageId" in state && state.ochaskGuardrailMessageId === s.message.id;
  });
  const isHiddenToolMessage = useAuiState(
    (s) => s.message.parts.length > 0 &&
      s.message.parts.every((part) => isHiddenToolCall(part, s.tools.toolUIs, Boolean(TaskGroupComponent))) &&
      !(s.message.status?.type === "incomplete" && s.message.status.reason === "error"),
  );

  if (isHiddenToolMessage && !progressLabel) return null;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative min-w-0 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-relaxed font-sans wrap-break-word"
      >
        {progressLabel && <ThinkingIndicator role="status" label={progressLabel} className="mb-2" />}
        <AnswerEvidence>
          <MessagePrimitive.GroupedParts groupBy={groupBy} indicator="never">
            {({ part, children }) => {
              switch (part.type) {
                case "group-chainOfThought":
                  return <div data-slot="aui_chain-of-thought">{children}</div>;
                case "group-task":
                  return TaskGroupComponent ? (
                    <TaskGroupComponent group={part} />
                  ) : null;
                case "group-tool":
                  if (ToolGroup) {
                    return <ToolGroup group={part}>{children}</ToolGroup>;
                  }
                  return <div className="flex flex-col gap-1.5">{children}</div>;
                case "group-reasoning": {
                  if (ReasoningGroup) {
                    return (
                      <ReasoningGroup group={part}>{children}</ReasoningGroup>
                    );
                  }
                  const running = part.status.type === "running";
                  return (
                    <ReasoningRoot streaming={running}>
                      <ReasoningTrigger
                        active={running}
                        duration={reasoningDuration(part.timing)}
                      />
                      <ReasoningContent aria-busy={running}>
                        <ReasoningText>{children}</ReasoningText>
                      </ReasoningContent>
                    </ReasoningRoot>
                  );
                }
                case "text":
                  return isScopeRefusal ? (
                    <GuardrailNotice
                      role="status"
                      title="Outside the Bearing Witness Research Assistant’s scope"
                      explanation={part.text}
                      policy="Topic scope"
                      alternatives={[]}
                    />
                  ) : <AnswerMarkdown />;
                case "reasoning":
                  return <Reasoning {...part} />;
                case "tool-call":
                  if (isHiddenToolCall(part, toolUIs)) return null;
                  if (isMapTool(part.toolName)) {
                    return <GeoMapToolCall {...part} />;
                  }
                  if (part.toolUI) return part.toolUI;
                  return <ToolFallbackComponent {...part} />;
                case "data":
                  return null;
                case "file":
                  return (
                    <div data-slot="aui_assistant-message-file" className="py-1">
                      <MessageFile {...part} />
                    </div>
                  );
                case "image":
                  return (
                    <div data-slot="aui_assistant-message-image" className="py-1">
                      <Image {...part} />
                    </div>
                  );
                default:
                  return null;
              }
            }}
          </MessagePrimitive.GroupedParts>
        </AnswerEvidence>
        <MessageError />
      </div>

      <BranchPicker className="ms-2" />
    </MessagePrimitive.Root>
  );
};

const CopyMessage: FC = () => (
  <ActionBarPrimitive.Copy
    render={<TooltipIconButton tooltip="Copy" aria-label="Copy message" className="size-7" />}
  >
    <AuiIf condition={(s) => s.message.isCopied}><CheckIcon /></AuiIf>
    <AuiIf condition={(s) => !s.message.isCopied}><CopyIcon /></AuiIf>
  </ActionBarPrimitive.Copy>
);

const MessageFile: FileMessagePartComponent = (part) => {
  const image = part.mimeType.toLowerCase().startsWith("image/")
    ? getFileHref(part.data, part.mimeType, part.sourceType)
    : null;
  if (!image) return <File {...part} />;
  return (
    <Image
      type="image"
      image={image}
      status={part.status}
      {...(part.filename !== undefined && { filename: part.filename })}
    />
  );
};

const UserFilePart: FileMessagePartComponent = (part) => (
  <div data-slot="aui_user-message-file" className="py-1">
    <MessageFile {...part} />
  </div>
);

const UserImagePart: ImageMessagePartComponent = (part) => (
  <div data-slot="aui_user-message-image" className="py-1">
    <Image {...part} />
  </div>
);

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content peer bg-muted text-foreground font-sans rounded-(--composer-radius) px-5 py-3 wrap-break-word empty:hidden">
          <MessagePrimitive.Parts
            components={{ File: UserFilePart, Image: UserImagePart }}
          />
        </div>
        <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>

      <BranchPicker
        data-slot="aui_user-branch-picker"
        className="col-span-full col-start-1 -me-1 justify-end"
      />
    </MessagePrimitive.Root>
  );
};

const UserActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-user-action-bar-root flex flex-col items-end"
    >
      <ActionBarPrimitive.Edit render={<TooltipIconButton tooltip="Edit" className="aui-user-action-edit" />}><PencilIcon /></ActionBarPrimitive.Edit>
    </ActionBarPrimitive.Root>
  );
};

const EditComposer: FC = () => {
  const { canSend } = useChatRunSettings();
  return (
    <MessagePrimitive.Root
      data-slot="aui_edit-composer-wrapper"
      className="flex flex-col px-2 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <ComposerPrimitive.Root
        className="aui-edit-composer-root border-foreground/10 focus-within:border-foreground/25 ms-auto flex w-full max-w-[85%] cursor-text flex-col rounded-(--composer-radius) border bg-(--composer-bg) transition-[border-color]"
        onSubmit={(event) => { if (!canSend) event.preventDefault(); }}
      >
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none"
          autoFocus
          submitMode={canSend ? "enter" : "none"}
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel render={<Button variant="ghost" size="sm" className="h-8 px-3" />}>Cancel
                              </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send disabled={!canSend} render={<Button size="sm" className="h-8 px-3" />}>Update
                              </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};

const BranchPicker: FC<BranchPickerPrimitive.Root.Props> = ({
  className,
  ...rest
}) => {
  return (
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className={cn(
        "aui-branch-picker-root text-muted-foreground -ms-2 me-2 inline-flex items-center text-xs",
        className,
      )}
      {...rest}
    >
      <BranchPickerPrimitive.Previous render={<TooltipIconButton tooltip="Previous" />}><ChevronLeftIcon /></BranchPickerPrimitive.Previous>
      <span className="aui-branch-picker-state font-medium">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next render={<TooltipIconButton tooltip="Next" />}><ChevronRightIcon /></BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  );
};
