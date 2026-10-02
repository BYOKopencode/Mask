import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, Code2, Image as ImageIcon, MessageSquare, Search, Video } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { CopyButton } from "@/components/site/CopyButton";
import { FamilyLogo, ModelLogo } from "@/components/site/ModelLogo";
import { GET_KEY_URL, SiteLayout } from "@/components/site/SiteLayout";
import { Sparkbars } from "@/components/site/Sparkbars";
import { fetchModels, fetchStats, PUBLIC_BASE_URL, type GatewayModel, type ModelType } from "@/lib/api";
import { fmtInt, fmtRelative } from "@/lib/format";
import {
  ALL_FAMILIES,
  capabilityTags,
  contextLabel,
  familyOf,
  FAMILY_ORDER,
  latencyLabel,
  maxOutputLabel,
  speedLabel,
} from "@/lib/models";
import { cn } from "@/lib/utils";

/** Smoothly animates a number toward its target. */
function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState<number>(target);
  const from = useRef<number>(target);
  useEffect(() => {
    const start = performance.now();
    const initial = from.current;
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(initial + (target - initial) * eased);
      if (progress < 1) frame = requestAnimationFrame(tick);
      else from.current = target;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration]);
  return value;
}

function Metric({ label, value, series, live }: { label: string; value: number; series: number[]; live?: boolean }) {
  const animated = useCountUp(value);
  return (
    <div className="min-w-0 flex-1 px-0 py-4 sm:px-5 sm:py-0 sm:first:pl-0">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {label}
        {live && <span className="mono rounded bg-ok/10 px-1.5 py-0.5 text-[10px] font-semibold text-ok">LIVE</span>}
      </div>
      <div className="mono tabular mt-2 truncate text-2xl font-medium tracking-tight sm:text-[28px]">{fmtInt(animated)}</div>
      <Sparkbars values={series} className="mt-3" />
    </div>
  );
}

const CAPABILITIES = [
  { icon: MessageSquare, title: "Chat & reasoning", body: "Frontier conversational and reasoning models for any use case." },
  { icon: Code2, title: "Coding agents", body: "Drop into Claude Code, Codex, OpenHands, Hermes, Cursor and Cline." },
  { icon: ImageIcon, title: "Vision & images", body: "Understand images and documents, or generate new images." },
  { icon: Video, title: "Video & speech", body: "Async video generation and text-to-speech on the same key." },
];

const TYPE_FILTERS: { key: "all" | ModelType; label: string }[] = [
  { key: "all", label: "All" },
  { key: "text", label: "Text" },
  { key: "image", label: "Image" },
  { key: "video", label: "Video" },
  { key: "speech", label: "Speech" },
];

function Spec({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="mono tabular mt-0.5 truncate text-[13px] text-foreground" title={value}>
        {value}
      </dd>
    </div>
  );
}

function ModelCard({ model, index }: { model: GatewayModel; index: number }) {
  const observed = model.observed;
  const recent = observed.last_observed ? Date.now() - new Date(observed.last_observed).getTime() < 72 * 3600_000 : false;
  return (
    <article
      className="panel animate-rise flex flex-col p-4 transition hover:border-[#3a3a40]"
      style={{ animationDelay: `${Math.min(index, 12) * 30}ms` }}
    >
      <div className="flex items-start gap-3">
        <ModelLogo modelId={model.id} size={34} />
        <div className="min-w-0 flex-1">
          <h3 className="mono truncate text-[14px] font-medium" title={model.id}>
            {model.id}
          </h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground">Last seen {fmtRelative(observed.last_observed)}</p>
        </div>
        <span className={cn("flex items-center gap-1.5 text-[11px]", recent ? "text-ok" : "text-muted-foreground")}>
          <span className={cn("h-1.5 w-1.5 rounded-full", recent ? "bg-ok" : "bg-muted-foreground")} />
          {recent ? "Online" : "Idle"}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {capabilityTags(model).map((tag) => (
          <span key={tag} className="rounded-md border border-border bg-raised px-2 py-0.5 text-[11px] text-muted-foreground">
            {tag}
          </span>
        ))}
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-x-3 gap-y-3 border-t border-border pt-3">
        <Spec label="Context" value={contextLabel(model)} />
        <Spec label="Max output" value={maxOutputLabel(model)} />
        <Spec label="Latency" value={latencyLabel(model)} />
        <Spec label="Requests" value={fmtInt(observed.requests)} />
        <Spec label="Speed" value={speedLabel(model)} />
        <Spec label="Avg output" value={observed.avg_output_tokens ? `${fmtInt(observed.avg_output_tokens)} tok` : "—"} />
      </dl>
    </article>
  );
}

const Index = () => {
  const statsQuery = useQuery({ queryKey: ["stats"], queryFn: ({ signal }) => fetchStats(signal), refetchInterval: 15_000 });
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: ({ signal }) => fetchModels(signal), refetchInterval: 60_000 });
  const [filter, setFilter] = useState<"all" | ModelType>("all");
  const [search, setSearch] = useState<string>("");

  const models = useMemo<GatewayModel[]>(() => modelsQuery.data?.models ?? [], [modelsQuery.data]);
  const stats = statsQuery.data;

  const familyCounts = useMemo(() => {
    const counts = new Map<string, number>();
    models.forEach((model) => {
      const key = familyOf(model.id).key;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    });
    return counts;
  }, [models]);

  const groups = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = models.filter(
      (model) => (filter === "all" || model.type === filter) && (!term || model.id.toLowerCase().includes(term)),
    );
    const map = new Map<string, GatewayModel[]>();
    filtered.forEach((model) => {
      const key = familyOf(model.id).key;
      map.set(key, [...(map.get(key) ?? []), model]);
    });
    return FAMILY_ORDER.filter((key) => map.has(key)).map((key) => ({
      family: familyOf(map.get(key)![0].id),
      models: map.get(key)!,
    }));
  }, [models, filter, search]);

  const endpoint = `${PUBLIC_BASE_URL}/v1/chat/completions`;
  const featuredModel = models.find((model) => model.type === "text")?.id ?? "claude-opus-4-7";

  return (
    <SiteLayout>
      <div className="mx-auto max-w-[1400px] px-4 sm:px-6 lg:px-10">
        <section className="grid gap-10 pb-10 pt-12 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:pt-16">
          <div className="animate-rise">
            <p className="flex items-center gap-2 text-sm font-medium text-ok">
              <span className="live-dot" /> All systems operational
            </p>
            <h1 className="mt-4 text-[40px] font-bold leading-[1.05] tracking-[-0.035em] sm:text-[52px]">
              One endpoint. Every model you actually want.
            </h1>
            <p className="mt-4 max-w-xl text-lg text-muted-foreground">
              Per-token billing, no subscriptions. OpenAI &amp; Anthropic compatible.
            </p>
            <ul className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-[15px]">
              {["No credit card required", "Instant setup", "Zero prompt retention"].map((item) => (
                <li key={item} className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-ok" /> {item}
                </li>
              ))}
            </ul>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to={GET_KEY_URL} className="btn-primary h-11 px-6">
                Get API key <ArrowRight className="h-4 w-4" />
              </Link>
              <Link to="/docs" className="btn-secondary h-11 px-6">
                Read the docs
              </Link>
            </div>
          </div>

          <div className="panel animate-rise p-5 sm:p-6" style={{ animationDelay: "80ms" }} aria-label="Live API activity">
            <div className="flex items-center justify-between">
              <span className="mono flex items-center gap-2 text-xs font-semibold tracking-wider text-ok">
                <span className="live-dot" /> LIVE
              </span>
              <span className="text-xs text-muted-foreground">Real-time API activity</span>
            </div>
            <div className="mt-5 flex flex-col divide-y divide-border sm:flex-row sm:divide-x sm:divide-y-0">
              <Metric label="Tokens processed" value={stats?.tokens_processed ?? 0} series={stats?.series.tokens ?? []} />
              <Metric label="Requests" value={stats?.requests ?? 0} series={stats?.series.requests ?? []} />
              <Metric label="Tokens / min" value={stats?.tokens_per_minute ?? 0} series={stats?.series.tokens ?? []} live />
            </div>
            <p className="mt-6 flex flex-wrap gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
              <span>{fmtInt(stats?.models ?? models.length)} models live</span>
              <span aria-hidden="true">•</span>
              <span>OpenAI &amp; Anthropic protocols</span>
              <span aria-hidden="true">•</span>
              <span>{stats?.source === "snapshot" ? "Snapshot data" : "Updated every 15s"}</span>
            </p>
          </div>
        </section>

        <section aria-label="Endpoint" className="panel flex flex-col gap-3 p-3 sm:p-4 lg:flex-row lg:items-center">
          <div className="flex min-w-0 items-center gap-3">
            <span className="mono shrink-0 rounded-md bg-raised px-2.5 py-1 text-xs font-semibold">POST</span>
            <code className="mono truncate text-sm">{endpoint}</code>
            <CopyButton value={endpoint} label="Copy endpoint" />
          </div>
          <div className="hidden h-8 w-px bg-border lg:block" />
          <code className="mono truncate text-[13px] text-muted-foreground lg:pl-1">
            {"{ "}
            <span className="text-[#7CA7FF]">"model"</span>: <span className="text-ok">"{featuredModel}"</span>,{" "}
            <span className="text-[#7CA7FF]">"stream"</span>: <span className="text-warn">true</span>
            {" }"}
          </code>
        </section>

        <section aria-labelledby="families" className="mt-12 flex flex-col gap-6 lg:flex-row lg:items-center">
          <h2 id="families" className="shrink-0 text-lg font-semibold leading-snug lg:w-48">
            Routes to every major model family
          </h2>
          <ul className="grid flex-1 grid-cols-2 gap-px overflow-hidden rounded-[10px] border border-border bg-border sm:grid-cols-4 xl:grid-cols-8">
            {ALL_FAMILIES.filter((family) => family.key !== "stepfun" && family.key !== "kira").map((family) => (
              <li key={family.key} className="flex items-center gap-3 bg-background px-4 py-4">
                <FamilyLogo logo={family.logo} mark={family.mark} tint={family.tint} invert={family.key === "openai" || family.key === "xai"} size={26} />
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{family.label}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {familyCounts.get(family.key) ? `${familyCounts.get(family.key)} models` : "Available"}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="Capabilities" className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {CAPABILITIES.map((item) => (
            <div key={item.title} className="panel flex gap-4 p-5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-raised">
                <item.icon className="h-5 w-5" />
              </span>
              <div>
                <h3 className="font-semibold">{item.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{item.body}</p>
              </div>
            </div>
          ))}
        </section>

        <section aria-labelledby="directory" className="mt-16 pb-20">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h2 id="directory" className="flex items-center gap-3 text-2xl font-bold tracking-tight">
                Live model directory
                <span className="mono rounded-md border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground">
                  {models.length}
                </span>
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Context and output limits per model, with latency and request counts observed on this gateway.
              </p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div role="tablist" aria-label="Model type" className="flex gap-1 rounded-lg border border-border p-1">
                {TYPE_FILTERS.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    role="tab"
                    aria-selected={filter === item.key}
                    onClick={() => setFilter(item.key)}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-medium transition",
                      filter === item.key ? "bg-raised text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <label className="relative">
                <span className="sr-only">Search models</span>
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search models…"
                  maxLength={60}
                  className="h-10 w-full rounded-lg border border-border bg-card pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:border-[#5f5f66] sm:w-56"
                />
              </label>
            </div>
          </div>

          {modelsQuery.isLoading ? (
            <div className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 8 }, (_, index) => (
                <div key={index} className="panel h-[188px] animate-pulse" />
              ))}
            </div>
          ) : groups.length === 0 ? (
            <div className="panel mt-8 p-10 text-center text-muted-foreground">No models match your filters.</div>
          ) : (
            groups.map((group) => (
              <div key={group.family.key} className="mt-10">
                <div className="mb-4 flex items-center gap-3">
                  <ModelLogo modelId={group.models[0].id} size={28} />
                  <h3 className="text-base font-semibold">{group.family.label}</h3>
                  <span className="text-sm text-muted-foreground">· {group.models.length} {group.models.length === 1 ? "model" : "models"}</span>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {group.models.map((model, index) => (
                    <ModelCard key={model.id} model={model} index={index} />
                  ))}
                </div>
              </div>
            ))
          )}
        </section>
      </div>
    </SiteLayout>
  );
};

export default Index;
