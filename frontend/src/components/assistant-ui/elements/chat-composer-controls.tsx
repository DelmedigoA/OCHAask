"use client";

import { useState } from "react";
import { DropdownMenu, Popover } from "radix-ui";
import { ArchiveIcon, ChevronDownIcon, LibraryBigIcon } from "lucide-react";
import { effortLabels, useChatRunSettings } from "@/lib/chat-run-settings";
import { cn } from "@/lib/utils";
import { ComposerMenu, ComposerModelItem, ComposerModelTrigger } from "./composer";
import { ReasoningEffort } from "./reasoning-effort";

const SOURCE_OPTIONS = [
  { key: "archive", label: "Archive", Icon: ArchiveIcon },
  { key: "ocha", label: "OCHA", Icon: LibraryBigIcon },
] as const;

export function ChatComposerControls() {
  const { model, sources, effort, setEffort, toggleSource } = useChatRunSettings();
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [effortOpen, setEffortOpen] = useState(false);

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-1 gap-y-1.5">
      {model && (
        <DropdownMenu.Root open={modelMenuOpen} onOpenChange={setModelMenuOpen}>
          <DropdownMenu.Trigger asChild>
            <ComposerModelTrigger model={model.label} open={modelMenuOpen} aria-label={`Model: ${model.label}`} />
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content asChild side="top" sideOffset={6} align="start">
              <ComposerMenu>
                <DropdownMenu.RadioGroup value={model.id}>
                  <DropdownMenu.RadioItem asChild value={model.id}>
                    <ComposerModelItem label={model.label} selected />
                  </DropdownMenu.RadioItem>
                </DropdownMenu.RadioGroup>
              </ComposerMenu>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      )}
      {model && effort && (
        <Popover.Root open={effortOpen} onOpenChange={setEffortOpen}>
          <Popover.Trigger
            type="button"
            className="text-muted-foreground hover:bg-accent hover:text-foreground inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs focus-visible:outline-ring"
            aria-label={`Reasoning effort: ${effortLabels[effort]}`}
          >
            {effortLabels[effort]}
            <ChevronDownIcon className="size-3 opacity-60" aria-hidden />
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content asChild side="top" sideOffset={6} align="start">
              <ComposerMenu className="p-3" aria-label="Reasoning effort">
                <ReasoningEffort
                  levels={model.reasoningEfforts}
                  selected={effort}
                  onSelect={(value) => {
                    setEffort(value);
                    setEffortOpen(false);
                  }}
                />
              </ComposerMenu>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      )}
      <div role="group" aria-label="Evidence sources" className="flex flex-wrap items-center gap-1">
        {SOURCE_OPTIONS.map(({ key, label, Icon }) => (
          <button
            key={key}
            type="button"
            aria-pressed={sources[key]}
            onClick={() => toggleSource(key)}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors focus-visible:outline-ring",
              sources[key]
                ? "border-primary/25 bg-primary/8 text-foreground hover:bg-primary/12"
                : "border-border text-muted-foreground hover:bg-accent",
            )}
          >
            <Icon className="size-3.5" aria-hidden />
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
