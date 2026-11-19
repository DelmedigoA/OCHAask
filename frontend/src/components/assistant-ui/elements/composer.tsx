"use client";

import type { ComponentProps } from "react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { floating, paper } from "./surfaces";

export function ComposerBar({
  dragActive = false,
  className,
  ...props
}: ComponentProps<"div"> & { dragActive?: boolean }) {
  return (
    <div
      data-slot="composer-bar"
      data-drag-active={dragActive || undefined}
      className={cn(
        paper,
        "flex w-full flex-col gap-2 rounded-[24px] p-2.5 transition-colors",
        dragActive && "bg-blue-500/[0.04] dark:bg-blue-500/10",
        className,
      )}
      {...props}
    />
  );
}

export function ComposerToolbar({
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-slot="composer-toolbar"
      className={cn("flex items-center justify-between", className)}
      {...props}
    />
  );
}

export function ComposerActions({
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-slot="composer-actions"
      className={cn("flex items-center gap-1.5", className)}
      {...props}
    />
  );
}

export function ComposerMenu({
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-slot="composer-menu"
      className={cn(floating, "z-50 min-w-44 rounded-xl p-1.5 outline-none", className)}
      {...props}
    />
  );
}

export function ComposerModelTrigger({
  model,
  open,
  className,
  ...props
}: Omit<ComponentProps<"button">, "children"> & {
  model: string;
  open: boolean;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      data-slot="composer-model-trigger"
      className={cn(
        "text-muted-foreground hover:bg-accent hover:text-foreground flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-normal transition-colors",
        className,
      )}
      {...props}
    >
      {model}
      <ChevronDownIcon className="size-3 opacity-60" />
    </button>
  );
}

export function ComposerModelItem({
  label,
  selected,
  className,
  ...props
}: ComponentProps<"button"> & { label: string; selected: boolean }) {
  return (
    <button
      type="button"
      data-slot="composer-menu-item"
      className={cn(
        "flex w-full items-center gap-3 rounded-lg px-3 py-1.5 text-start text-sm outline-none data-highlighted:bg-accent",
        className,
      )}
      {...props}
    >
      <span className="flex-1">{label}</span>
      {selected && <CheckIcon className="size-3.5" aria-hidden />}
    </button>
  );
}
