import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  Clock,
  Database,
  FileText,
  RefreshCw,
} from "lucide-react";
import { memo, useCallback, useMemo, useState } from "react";

import { CopyButton } from "@/components/site/CopyButton";
import { ModelLogo } from "@/components/site/ModelLogo";
import { SiteLayout } from "@/components/site/SiteLayout";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import {
  fetchModels,
  fetchRequests,
  type RangeFilter,
  type RequestQuery,
  type RequestRow,
  type SortKey,
} from "@/lib/api";
import { fmtCompact, fmtDateTime, fmtInt, fmtMs, fmtTime, fmtTokens, STATUS_TEXT } from "@/lib/format";
import { cn } from "@/lib/utils";

const RANGES: { key: RangeFilter; label: string }[] = [
  { key: "1h", label: "Last hour" },
  { key: "24h", label: "Last 24h" },
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
];

function statusTone(status: number): { text: string; bg: string; dot: string } {
  if (status < 400) return { text: "text-ok", bg: "bg-ok/10", dot: "bg-ok" };
  if (status < 500) return { text: "text-warn", bg: "bg-warn/10", dot: "bg-warn" };
  return { text: "text-err", bg: "bg-err/10", dot: "bg-err" };
}

function StatusBadge({ status, long = false }: { status: number; long?: boolean }) {
  const tone = statusTone(status);
  return (
    <span className={cn("mono inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium", tone.bg, tone.text)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", tone.dot)} />
      {status}
      {long && STATUS_TEXT[status] ? ` ${STATUS_TEXT[status]}` : ""}
    </span>
  );
}

function Tile({ icon: Icon, label, value, tone }: { icon: typeof FileText; label: string; value: string; tone?: string }) {
  return (
    <div className="panel flex items-center gap-4 p-5">
      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-border bg-raised">
        <Icon className={cn("h-5 w-5", tone)} />
      </span>
      <div className="min-w-0">
        <div className="text-sm text-muted-foreground">{label}</div>
        <div className="mono tabular mt-1 truncate text-2xl font-medium tracking-tight">{value}</div>
      </div>
    </div>
  );
}

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "model", label: "Model" },
  { key: "status", label: "Status" },
  { key: "input", label: "Input tok", align: "right" },
  { key: "output", label: "Output tok", align: "right" },
  { key: "latency", label: "Latency", align: "right" },
  { key: "ttft", label: "TTFT", align: "right" },
  { key: "time", label: "Time", align: "right" },
];

const Row = memo(function Row({ row, onOpen }: { row: RequestRow; onOpen: (row: RequestRow) => void }) {
  const failed = row.status >= 400;
  return (
    <tr
      tabIndex={0}
      onClick={() => onOpen(row)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(row);
        }
      }}
      className="cursor-pointer border-t border-border transition hover:bg-white/[0.025] focus-visible:bg-white/[0.04] focus-visible:outline-none"
      aria-label={`${row.model}, status ${row.status}, open details`}
    >
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-3">
          <ModelLogo modelId={row.model} size={30} />
          <span className="mono truncate text-[13px]">{row.model}</span>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <StatusBadge status={row.status} />
      </td>
      <td className="mono tabular px-5 py-3.5 text-right text-[13px]">{failed && !row.input_tokens ? "—" : fmtInt(row.input_tokens)}</td>
      <td className="mono tabular px-5 py-3.5 text-right text-[13px]">{failed && !row.output_tokens ? "—" : fmtInt(row.output_tokens)}</td>
      <td className="mono tabular px-5 py-3.5 text-right text-[13px]">{fmtMs(row.latency_ms)}</td>
      <td className="mono tabular px-5 py-3.5 text-right text-[13px] text-muted-foreground">{fmtMs(row.ttft_ms)}</td>
      <td className="mono tabular px-5 py-3.5 text-right text-[13px]">
        <time dateTime={row.created_at} title={fmtDateTime(row.created_at)}>
          {fmtTime(row.created_at)}
        </time>
      </td>
    </tr>
  );
});

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="mono tabular text-sm">{value}</dd>
    </div>
  );
}

function RequestDrawer({
  row,
  contextWindow,
  onClose,
}: {
  row: RequestRow | null;
  contextWindow: number | null;
  onClose: () => void;
}) {
  const ttft = row?.ttft_ms ?? null;
  const total = row?.latency_ms ?? 0;
  const generation = ttft !== null ? Math.max(0, total - ttft) : null;
  const ttftPct = ttft !== null && total > 0 ? Math.min(100, (ttft / total) * 100) : 0;
  const contextUsed = row && contextWindow ? (row.input_tokens / contextWindow) * 100 : null;

  return (
    <Sheet open={row !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto border-border bg-card p-0 sm:max-w-md">
        {row && (
          <div className="p-6">
            <div className="flex items-start gap-4 pr-8">
              <ModelLogo modelId={row.model} size={48} />
              <div className="min-w-0">
                <SheetTitle className="mono truncate text-xl font-semibold">{row.model}</SheetTitle>
                <SheetDescription className="mt-2 flex flex-wrap items-center gap-3">
                  <StatusBadge status={row.status} long />
                  <span className="mono flex items-center gap-1 text-xs text-muted-foreground">
                    {row.id}
                    <CopyButton value={row.id} label="Copy request id" className="h-6 w-6 border-0 bg-transparent" />
                  </span>
                </SheetDescription>
              </div>
            </div>

            <section className="mt-6 border-t border-border pt-5">
              <h3 className="font-semibold">Request details</h3>
              <dl className="mt-2">
                <DetailRow label="Latency" value={fmtMs(row.latency_ms)} />
                <DetailRow label="Time to first token" value={fmtMs(row.ttft_ms)} />
                <DetailRow label="Input tokens" value={fmtInt(row.input_tokens)} />
                <DetailRow label="Output tokens" value={fmtInt(row.output_tokens)} />
                <DetailRow label="Total tokens" value={fmtInt(row.total_tokens)} />
                <DetailRow label="Speed" value={row.tokens_per_second ? `${fmtInt(row.tokens_per_second)} tok/s` : "—"} />
                <DetailRow
                  label="Context used"
                  value={contextUsed !== null && contextWindow ? `${contextUsed < 0.1 ? "<0.1" : contextUsed.toFixed(1)}% of ${fmtTokens(contextWindow)}` : "—"}
                />
                <DetailRow label="Streamed" value={row.streamed ? "Yes" : "No"} />
              </dl>
            </section>

            <section className="mt-5 border-t border-border pt-5">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">Latency timeline</h3>
                <span className="mono text-sm text-muted-foreground">{fmtMs(total)}</span>
              </div>
              <div className="mt-4 flex h-2 overflow-hidden rounded-full bg-raised" aria-hidden="true">
                {ttft !== null ? (
                  <>
                    <span className="bg-info" style={{ width: `${Math.max(2, ttftPct)}%` }} />
                    <span className="flex-1 bg-ok" />
                  </>
                ) : (
                  <span className={cn("flex-1", row.status < 400 ? "bg-ok" : "bg-err")} />
                )}
              </div>
              <dl className="mt-4 space-y-2 text-sm">
                {ttft !== null ? (
                  <>
                    <div className="flex justify-between">
                      <dt className="flex items-center gap-2 text-muted-foreground"><span className="h-2 w-2 rounded-full bg-info" /> First token</dt>
                      <dd className="mono">{fmtMs(ttft)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="flex items-center gap-2 text-muted-foreground"><span className="h-2 w-2 rounded-full bg-ok" /> Generation</dt>
                      <dd className="mono">{fmtMs(generation)}</dd>
                    </div>
                  </>
                ) : (
                  <div className="flex justify-between">
                    <dt className="flex items-center gap-2 text-muted-foreground">
                      <span className={cn("h-2 w-2 rounded-full", row.status < 400 ? "bg-ok" : "bg-err")} /> End-to-end
                    </dt>
                    <dd className="mono">{fmtMs(total)}</dd>
                  </div>
                )}
              </dl>
            </section>

            <section className="mt-5 border-t border-border pt-5">
              <dl className="space-y-2.5 text-sm">
                <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Request ID</dt><dd className="mono">{row.id}</dd></div>
                <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Time</dt><dd className="mono">{fmtDateTime(row.created_at)}</dd></div>
                <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Protocol</dt><dd className="mono">{row.api}</dd></div>
                <div className="flex justify-between gap-4"><dt className="text-muted-foreground">Region</dt><dd className="mono">Global</dd></div>
              </dl>
              <p className="mt-5 rounded-lg border border-border bg-raised p-3 text-xs text-muted-foreground">
                Only telemetry is recorded. Prompts, responses, keys and caller addresses are never stored or shown.
              </p>
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function pageList(page: number, pages: number): (number | "…")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const set = new Set<number>([1, pages, page - 1, page, page + 1].filter((value) => value >= 1 && value <= pages));
  const sorted = [...set].sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  sorted.forEach((value, index) => {
    if (index && value - sorted[index - 1] > 1) out.push("…");
    out.push(value);
  });
  return out;
}

const PAGE_SIZE = 25;

const Requests = () => {
  const [query, setQuery] = useState<RequestQuery>({
    page: 1,
    size: PAGE_SIZE,
    model: "",
    status: "all",
    range: "24h",
    q: "",
    sort: "time",
    order: "desc",
  });
  const [live, setLive] = useState<boolean>(true);
  const [selected, setSelected] = useState<RequestRow | null>(null);

  const requestsQuery = useQuery({
    queryKey: ["requests", query],
    queryFn: ({ signal }) => fetchRequests(query, signal),
    placeholderData: keepPreviousData,
    refetchInterval: live ? 10_000 : false,
    retry: 1,
  });
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: ({ signal }) => fetchModels(signal) });

  const contextByModel = useMemo(() => {
    const map = new Map<string, number | null>();
    modelsQuery.data?.models.forEach((model) => map.set(model.id, model.context_window));
    return map;
  }, [modelsQuery.data]);

  const onSort = useCallback(
    (key: SortKey) =>
      setQuery((prev) => ({ ...prev, page: 1, sort: key, order: prev.sort === key && prev.order === "desc" ? "asc" : "desc" })),
    [],
  );
  const onOpen = useCallback((row: RequestRow) => setSelected(row), []);

  const data = requestsQuery.data;
  const summary = data?.summary;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  const rangeLabel = RANGES.find((range) => range.key === query.range)?.label ?? "";
  const first = data && data.total ? (data.page - 1) * data.size + 1 : 0;
  const last = data ? Math.min(data.total, data.page * data.size) : 0;

  return (
    <SiteLayout>
      <div className="mx-auto max-w-[1400px] px-4 pb-20 pt-10 sm:px-6 lg:px-10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-4xl font-bold tracking-tight">Requests</h1>
            <p className="mt-2 text-muted-foreground">Live request logs and performance metrics across all models.</p>
          </div>
          <button
            type="button"
            onClick={() => setLive((value) => !value)}
            aria-pressed={live}
            className="btn-secondary h-9 self-start sm:self-auto"
          >
            {live ? <span className="live-dot" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {live ? "Live" : "Paused"}
          </button>
        </div>

        <section aria-label="Summary" className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Tile icon={FileText} label={`Requests · ${rangeLabel}`} value={fmtInt(summary?.requests ?? null)} />
          <Tile icon={CircleCheck} label="Success rate" value={summary?.success_rate != null ? `${summary.success_rate}%` : "—"} tone="text-ok" />
          <Tile icon={Clock} label="Avg latency" value={fmtMs(summary?.avg_latency_ms ?? null)} />
          <Tile icon={Database} label="Tokens" value={fmtCompact(summary?.tokens ?? null)} />
        </section>

        <section aria-label="Request log" className="panel mt-4 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left">
              <thead>
                <tr className="text-sm text-muted-foreground">
                  {COLUMNS.map((column) => {
                    const active = query.sort === column.key;
                    const Icon = !active ? ArrowUpDown : query.order === "asc" ? ArrowUp : ArrowDown;
                    return (
                      <th
                        key={column.key}
                        scope="col"
                        aria-sort={active ? (query.order === "asc" ? "ascending" : "descending") : "none"}
                        className={cn("px-5 py-4 font-medium", column.align === "right" && "text-right")}
                      >
                        <button
                          type="button"
                          onClick={() => onSort(column.key)}
                          className={cn("inline-flex items-center gap-1.5 hover:text-foreground", active && "text-foreground")}
                        >
                          {column.label}
                          <Icon className="h-3.5 w-3.5 opacity-60" />
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {requestsQuery.isLoading &&
                  Array.from({ length: 6 }, (_, index) => (
                    <tr key={index} className="border-t border-border">
                      <td colSpan={7} className="px-5 py-4">
                        <div className="h-6 animate-pulse rounded bg-raised" />
                      </td>
                    </tr>
                  ))}
                {data?.requests.map((row) => <Row key={row.id} row={row} onOpen={onOpen} />)}
              </tbody>
            </table>
            {requestsQuery.isError && !data && (
              <div className="border-t border-border px-5 py-14 text-center">
                <p className="font-medium">Request telemetry is unavailable right now.</p>
                <p className="mt-1 text-sm text-muted-foreground">{(requestsQuery.error as Error).message}</p>
                <button type="button" onClick={() => requestsQuery.refetch()} className="btn-primary mt-5">
                  <RefreshCw className="h-4 w-4" /> Retry
                </button>
              </div>
            )}
            {data && data.requests.length === 0 && (
              <div className="border-t border-border px-5 py-14 text-center text-muted-foreground">
                No requests match these filters in the selected range.
              </div>
            )}
          </div>
          <div className="flex flex-col gap-3 border-t border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">
              {data ? `Showing ${fmtInt(first)}–${fmtInt(last)} of ${fmtInt(data.total)} requests` : " "}
            </p>
            <nav aria-label="Pagination" className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Previous page"
                disabled={query.page <= 1}
                onClick={() => setQuery((prev) => ({ ...prev, page: prev.page - 1 }))}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-border disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              {pageList(query.page, pages).map((item, index) =>
                item === "…" ? (
                  <span key={`gap-${index}`} className="px-2 text-sm text-muted-foreground">…</span>
                ) : (
                  <button
                    key={item}
                    type="button"
                    aria-current={item === query.page ? "page" : undefined}
                    onClick={() => setQuery((prev) => ({ ...prev, page: item }))}
                    className={cn(
                      "mono h-9 min-w-9 rounded-lg px-2.5 text-sm transition",
                      item === query.page ? "border border-border bg-raised text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {item}
                  </button>
                ),
              )}
              <button
                type="button"
                aria-label="Next page"
                disabled={query.page >= pages}
                onClick={() => setQuery((prev) => ({ ...prev, page: prev.page + 1 }))}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-border disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </nav>
          </div>
        </section>
      </div>
      <RequestDrawer row={selected} contextWindow={selected ? contextByModel.get(selected.model) ?? null : null} onClose={() => setSelected(null)} />
    </SiteLayout>
  );
};

export default Requests;
