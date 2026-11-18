"use client";

import { cn } from "@/lib/utils";
import { field } from "./surfaces";
import { effortLabels, type ReasoningEffort as Effort } from "@/lib/chat-run-settings";

export function ReasoningEffort({
  levels,
  selected,
  onSelect,
}: {
  levels: readonly Effort[];
  selected: Effort;
  onSelect: (level: Effort) => void;
}) {
  return (
    <div data-slot="reasoning-effort" className="flex w-full flex-col gap-2.5">
      <span className="text-sm font-medium">Reasoning effort</span>
      <div role="group" aria-label="Reasoning effort" className={cn(field, "flex gap-0.5 rounded-full p-0.5")}>
        {levels.map((level) => (
          <button
            key={level}
            type="button"
            aria-pressed={level === selected}
            onClick={() => onSelect(level)}
            className={cn(
              "flex-1 rounded-full px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-ring",
              level === selected ? "bg-background text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {effortLabels[level]}
          </button>
        ))}
      </div>
    </div>
  );
}
