import * as React from "react";
import { ResponsiveContainer } from "recharts";
import { cn } from "@/lib/utils";

export type ChartConfig = Record<string, { label?: string; color: string }>;

type ChartContainerProps = React.ComponentProps<"div"> & {
  config: ChartConfig;
  children: React.ComponentProps<typeof ResponsiveContainer>["children"];
};

function ChartContainer({ config, className, children, style, ...props }: ChartContainerProps) {
  const chartId = `chart-${React.useId().replaceAll(":", "")}`;
  const variables = Object.fromEntries(
    Object.entries(config).map(([key, value]) => [`--color-${key}`, value.color]),
  ) as React.CSSProperties;

  return (
    <div
      data-slot="chart"
      data-chart={chartId}
      className={cn(
        "flex justify-center text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line]:stroke-border/50 [&_.recharts-layer]:outline-hidden [&_.recharts-surface]:outline-hidden",
        className,
      )}
      style={{ ...variables, ...style }}
      {...props}
    >
      <ResponsiveContainer initialDimension={{ width: 640, height: 176 }}>
        {children}
      </ResponsiveContainer>
    </div>
  );
}

export { ChartContainer };
