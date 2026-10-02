import { memo } from "react";

interface SparkbarsProps {
  values: number[];
  bars?: number;
  className?: string;
}

/** Tiny bar chart for hero metrics; shows a calm idle pattern when no data. */
export const Sparkbars = memo(function Sparkbars({ values, bars = 12, className }: SparkbarsProps) {
  const series = values.length ? values.slice(-bars) : [3, 5, 4, 6, 5, 7, 4, 6, 8, 5, 7, 6];
  const max = Math.max(...series, 1);
  return (
    <div className={className} aria-hidden="true">
      <div className="flex h-5 items-end gap-[3px]">
        {series.map((value, index) => (
          <span
            key={index}
            className="w-[3px] rounded-sm bg-ok/80 transition-all duration-700"
            style={{ height: `${Math.max(18, (value / max) * 100)}%`, opacity: 0.45 + (index / series.length) * 0.55 }}
          />
        ))}
      </div>
    </div>
  );
});
