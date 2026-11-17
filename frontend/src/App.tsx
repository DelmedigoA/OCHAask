import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider, useAuiState } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import {
  BookOpenText,
  LibraryBig,
  MessageSquareText,
  Moon,
  Search,
  Sun,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ChatEffort } from "@/components/assistant-ui/elements/thread.aui";

type Page = "corpus" | "search" | "chat";
type Conversation = { id: string; title: string; created_at: string; updated_at: string };

const CorpusPage = lazy(() => import("@/components/corpus/corpus-page").then((module) => ({ default: module.CorpusPage })));
const SearchPage = lazy(() => import("@/components/search/search-page").then((module) => ({ default: module.SearchPage })));
const Thread = lazy(() => import("@/components/assistant-ui/elements/thread.aui").then((module) => ({ default: module.Thread })));
const ThreadList = lazy(() => import("@/components/assistant-ui/elements/thread-list.aui").then((module) => ({ default: module.ThreadList })));

function PageLoading() {
  return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground" role="status">Loading workspace…</div>;
}

function pageFromPath() {
  const pathname = window.location.pathname.replace(/\/$/, "");
  if (pathname === "/search") return "search" as const;
  if (pathname === "/chat") return "chat" as const;
  if (pathname !== "/corpus") window.history.replaceState(null, "", "/corpus");
  return "corpus" as const;
}

function savedChatEffort(): ChatEffort {
  const stored = localStorage.getItem("ochask-chat-effort");
  return stored === "low" || stored === "high" ? stored : "medium";
}

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));

  useEffect(() => {
    const stored = localStorage.getItem("ochask-theme");
    const nextDark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", nextDark);
    setDark(nextDark);
  }, []);

  function toggle() {
    const nextDark = !dark;
    document.documentElement.classList.toggle("dark", nextDark);
    localStorage.setItem("ochask-theme", nextDark ? "dark" : "light");
    setDark(nextDark);
  }

  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label={`Switch to ${dark ? "light" : "dark"} theme`} title={`Switch to ${dark ? "light" : "dark"} theme`}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}

function WorkspaceSidebar({ page, navigate }: { page: Page; navigate: (page: Page) => void }) {
  const { isMobile, setOpenMobile } = useSidebar();
  const items = [
    { page: "corpus" as const, label: "Corpus", icon: LibraryBig },
    { page: "search" as const, label: "Search", icon: Search },
    { page: "chat" as const, label: "Chat", icon: MessageSquareText },
  ];

  function openPage(nextPage: Page) {
    navigate(nextPage);
    if (isMobile) setOpenMobile(false);
  }

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-16 justify-center border-b px-3 py-2">
        <SidebarMenu>
          <SidebarMenuItem>
            <div className="flex h-12 items-center gap-2 overflow-hidden rounded-md px-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background text-foreground">
                <BookOpenText className="size-4" aria-hidden="true" />
              </div>
              <div className="flex min-w-0 flex-col gap-0.5 leading-none group-data-[collapsible=icon]:hidden">
                <span className="font-semibold tracking-tight">OCHAsk</span>
                <span className="text-xs text-muted-foreground">Research workspace</span>
              </div>
            </div>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent className="gap-2 px-2 py-3">
        <nav aria-label="Workspace pages">
          <SidebarMenu>
            {items.map(({ page: itemPage, label, icon: Icon }) => (
              <SidebarMenuItem key={itemPage}>
                <SidebarMenuButton
                  isActive={page === itemPage}
                  tooltip={label}
                  render={
                    <a
                      href={`/${itemPage}`}
                      aria-current={page === itemPage ? "page" : undefined}
                      onClick={(event) => {
                        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                        event.preventDefault();
                        openPage(itemPage);
                      }}
                    />
                  }
                >
                  <Icon aria-hidden="true" />
                  <span>{label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </nav>

        {page === "chat" && (
          <>
            <SidebarSeparator className="my-1" />
            <p className="px-2 text-xs font-medium text-muted-foreground group-data-[collapsible=icon]:hidden">Recent chats</p>
            <div className="min-h-0 px-1 group-data-[collapsible=icon]:hidden">
              <Suspense fallback={<p className="px-2 py-2 text-xs text-muted-foreground">Loading chats…</p>}>
                <ThreadList />
              </Suspense>
            </div>
          </>
        )}
      </SidebarContent>

      <SidebarFooter className="border-t px-4 py-3 group-data-[collapsible=icon]:px-2">
        <p className="text-xs text-muted-foreground group-data-[collapsible=icon]:sr-only">OCHA humanitarian reports</p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

function ConversationPersistence({ conversationId, onSaved }: { conversationId: string; onSaved: () => void }) {
  const messages = useAuiState((state) => state.thread.messages);
  const isRunning = useAuiState((state) => state.thread.isRunning);

  useEffect(() => {
    if (!conversationId || !messages.length || isRunning) return;
    const title = messages.find((message) => message.role === "user")?.content
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join(" ")
      .trim()
      .slice(0, 64) || "Research chat";
    const timer = window.setTimeout(() => {
      void api(`/api/conversations/${conversationId}`, {
        method: "PUT",
        body: JSON.stringify({ title, messages }),
      }).then(onSaved).catch(() => {});
    }, 500);
    return () => window.clearTimeout(timer);
  }, [conversationId, isRunning, messages, onSaved]);

  return null;
}

function WorkspaceLayout({
  page,
  navigate,
  children,
}: {
  page: Page;
  navigate: (page: Page) => void;
  children: React.ReactNode;
}) {
  return (
    <SidebarProvider defaultOpen className="h-dvh min-h-0">
      <div className="flex h-full w-full">
        <WorkspaceSidebar page={page} navigate={navigate} />
        <SidebarInset className="min-h-0 overflow-hidden">
          <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 sm:px-5">
            <SidebarTrigger />
            <ThemeToggle />
          </header>
          {children}
        </SidebarInset>
      </div>
    </SidebarProvider>
  );
}

function ChatWorkspace({ page, navigate }: { page: Page; navigate: (page: Page) => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<string>(() => crypto.randomUUID());
  const [effort, setEffort] = useState<ChatEffort>(savedChatEffort);
  const effortRef = useRef(effort);
  effortRef.current = effort;
  const [chatMounted, setChatMounted] = useState(page === "chat");
  const updateEffort = useCallback((nextEffort: ChatEffort) => {
    setEffort(nextEffort);
    localStorage.setItem("ochask-chat-effort", nextEffort);
  }, []);
  const reloadConversations = useCallback(() => {
    void api<{ conversations: Conversation[] }>("/api/conversations")
      .then((data) => setConversations(data.conversations))
      .catch(() => {});
  }, []);
  const createConversation = useCallback(async () => {
    const result = await api<{ id: string }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({}),
    });
    setCurrentId(result.id);
    reloadConversations();
  }, [reloadConversations]);

  useEffect(() => { reloadConversations(); }, [reloadConversations]);
  useEffect(() => { if (page === "chat") setChatMounted(true); }, [page]);

  const agent = useMemo(
    () => new HttpAgent({
      url: "/api/chat",
      threadId: currentId,
      fetch: (url, init) => {
        const headers = new Headers(init.headers);
        headers.set("X-OCHAsk-Reasoning-Effort", effortRef.current);
        return window.fetch(url, { ...init, headers });
      },
    }),
    [currentId],
  );
  const runtime = useAgUiRuntime({
    agent,
    adapters: {
      threadList: {
        threadId: currentId,
        threads: conversations.map((conversation) => ({
          id: conversation.id,
          status: "regular" as const,
          title: conversation.title,
          custom: { updatedAt: conversation.updated_at },
        })),
        onSwitchToNewThread: createConversation,
        onSwitchToThread: async (threadId: string) => {
          const loaded = await api<{ messages: never[] }>(`/api/conversations/${threadId}/load`);
          setCurrentId(threadId);
          return { messages: loaded.messages };
        },
        onDelete: async (threadId: string) => {
          await api(`/api/conversations/${threadId}`, { method: "DELETE", body: "{}" });
          if (threadId === currentId) await createConversation();
          reloadConversations();
        },
        onRename: async (threadId: string, title: string) => {
          const loaded = await api<{ messages: never[] }>(`/api/conversations/${threadId}/load`);
          await api(`/api/conversations/${threadId}`, {
            method: "PUT",
            body: JSON.stringify({ title, messages: loaded.messages }),
          });
          reloadConversations();
        },
      },
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ConversationPersistence conversationId={currentId} onSaved={reloadConversations} />
      <WorkspaceLayout page={page} navigate={navigate}>
        {page === "corpus" && <div className="flex-1 overflow-y-auto"><Suspense fallback={<PageLoading />}><CorpusPage /></Suspense></div>}
        {page === "search" && <div className="flex-1 overflow-y-auto"><Suspense fallback={<PageLoading />}><SearchPage /></Suspense></div>}
        {chatMounted && <div className={page === "chat" ? "flex min-h-0 flex-1 overflow-hidden" : "hidden"} aria-hidden={page !== "chat"}>
          <Suspense fallback={<PageLoading />}><Thread autoFocus={false} effort={effort} onEffortChange={updateEffort} /></Suspense>
        </div>}
      </WorkspaceLayout>
    </AssistantRuntimeProvider>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(pageFromPath);
  const navigate = useCallback((nextPage: Page) => {
    if (window.location.pathname !== `/${nextPage}`) {
      window.history.pushState(null, "", `/${nextPage}`);
    }
    setPage(nextPage);
  }, []);

  useEffect(() => {
    const onPopState = () => setPage(pageFromPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  return (
    <TooltipProvider>
      <ChatWorkspace page={page} navigate={navigate} />
    </TooltipProvider>
  );
}
