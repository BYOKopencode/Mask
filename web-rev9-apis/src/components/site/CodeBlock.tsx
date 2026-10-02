import { memo, useMemo, useState } from "react";

import { CopyButton } from "@/components/site/CopyButton";
import { cn } from "@/lib/utils";

export interface CodeTab {
  label: string;
  code: string;
}

/** Lightweight token colouring: strings, comments, keywords, numbers. */
function highlight(code: string): string {
  const escaped = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped.replace(
    /(#[^\n]*|\/\/[^\n]*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|\b(export|import|from|const|await|async|for|in|of|return|def|function|package|func|if|true|false|None|null|new|print)\b|\b(\d+(?:\.\d+)?)\b/g,
    (match, comment: string, str: string, keyword: string, num: string) => {
      if (comment) return `<span style="color:#6b6b73">${comment}</span>`;
      if (str) return `<span style="color:#3DDC97">${str}</span>`;
      if (keyword) return `<span style="color:#7CA7FF">${keyword}</span>`;
      if (num) return `<span style="color:#F5B544">${num}</span>`;
      return match;
    },
  );
}

interface CodeBlockProps {
  tabs: CodeTab[];
  title?: string;
  className?: string;
  lineNumbers?: boolean;
  maxHeight?: number;
}

export const CodeBlock = memo(function CodeBlock({ tabs, title, className, lineNumbers = false, maxHeight }: CodeBlockProps) {
  const [active, setActive] = useState<number>(0);
  const current = tabs[Math.min(active, tabs.length - 1)];
  const html = useMemo(() => highlight(current.code), [current.code]);
  const lines = useMemo(() => current.code.split("\n").length, [current.code]);

  return (
    <div className={cn("overflow-hidden rounded-[10px] border border-border bg-[#0D0D0F]", className)}>
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        {title && tabs.length === 1 ? (
          <span className="mono px-2 text-xs text-muted-foreground">{title}</span>
        ) : (
          <div role="tablist" className="flex min-w-0 gap-1 overflow-x-auto">
            {tabs.map((tab, index) => (
              <button
                key={tab.label}
                type="button"
                role="tab"
                aria-selected={index === active}
                onClick={() => setActive(index)}
                className={cn(
                  "whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium transition",
                  index === active ? "bg-raised text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}
        <div className="ml-auto pl-2">
          <CopyButton value={current.code} variant="solid" />
        </div>
      </div>
      <div className="overflow-auto" style={maxHeight ? { maxHeight } : undefined}>
        <div className="flex min-w-max">
          {lineNumbers && (
            <pre aria-hidden="true" className="mono select-none py-4 pl-4 pr-3 text-right text-[12.5px] leading-6 text-[#4a4a52]">
              {Array.from({ length: lines }, (_, index) => index + 1).join("\n")}
            </pre>
          )}
          <pre className="mono flex-1 px-4 py-4 text-[12.5px] leading-6 text-[#D6D6D2]">
            <code dangerouslySetInnerHTML={{ __html: html }} />
          </pre>
        </div>
      </div>
    </div>
  );
});
