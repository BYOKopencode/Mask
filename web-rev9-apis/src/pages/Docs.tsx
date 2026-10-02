import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Bot, Loader2, Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CodeBlock } from "@/components/site/CodeBlock";
import { CopyButton } from "@/components/site/CopyButton";
import { ModelLogo } from "@/components/site/ModelLogo";
import { SiteLayout } from "@/components/site/SiteLayout";
import {
  AGENTS,
  AUTH_CODE,
  BASE,
  ENDPOINTS,
  ERROR_ENVELOPE,
  ERRORS,
  JSON_CODE,
  MORE_AGENTS,
  QUICKSTART,
  RESPONSES_CODE,
  RESPONSES_PARAMS,
  RETRY_CODE,
  SDKS,
  STREAM_CODE,
  TOC,
  TOOLS_CODE,
  V1,
} from "@/data/docs";
import { API_BASE, fetchModels } from "@/lib/api";
import { fmtTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

function Section({ id, eyebrow, title, children }: { id: string; eyebrow?: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="border-t border-border pt-12 first:border-t-0 first:pt-0">
      {eyebrow && <p className="mono text-xs font-semibold uppercase tracking-[0.12em] text-ok">{eyebrow}</p>}
      <h2 id={`${id}-title`} className="mt-2 text-[28px] font-bold tracking-tight">
        {title}
      </h2>
      <div className="mt-4 space-y-5 text-[15px] leading-7 text-[#C9C9C5]">{children}</div>
    </section>
  );
}

function Code({ children }: { children: ReactNode }) {
  return <code className="inline-code">{children}</code>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-[10px] border border-border">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead className="bg-card text-muted-foreground">
          <tr>
            {head.map((cell) => (
              <th key={cell} scope="col" className="px-4 py-3 font-medium">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t border-border align-top">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-4 py-3 text-[#D6D6D2]">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AgentSection() {
  const [active, setActive] = useState<string>(AGENTS[0].id);
  const agent = AGENTS.find((item) => item.id === active) ?? AGENTS[0];

  return (
    <Section id="agents" eyebrow="Integrations" title="Agent API Usage">
      <p>
        Point any coding agent at Rev9 Apis. Anthropic agents use <Code>/v1/messages</Code>, OpenAI-style agents use{" "}
        <Code>/v1/chat/completions</Code>, and Codex uses <Code>/v1/responses</Code>. Tool calls, streaming and long contexts
        pass straight through.
      </p>
      <div role="tablist" aria-label="Agent" className="flex flex-wrap gap-1 rounded-[10px] border border-border bg-card p-1.5">
        {AGENTS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`agent-tab-${item.id}`}
            aria-selected={item.id === active}
            aria-controls="agent-panel"
            onClick={() => setActive(item.id)}
            className={cn(
              "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition",
              item.id === active ? "bg-raised text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {item.logo ? (
              <img src={item.logo} alt="" className={cn("h-4 w-4", item.invert && "invert")} />
            ) : (
              <Bot className="h-4 w-4" />
            )}
            {item.name}
          </button>
        ))}
      </div>

      <div id="agent-panel" role="tabpanel" aria-labelledby={`agent-tab-${agent.id}`} className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="rounded-md border border-border bg-raised px-2 py-1 text-xs text-muted-foreground">{agent.protocol}</span>
          <span className="mono rounded-md border border-border bg-raised px-2 py-1 text-xs">{agent.route}</span>
        </div>
        <p>{agent.summary}</p>
        <h3 className="text-xl font-semibold text-foreground">Setup</h3>
        <ol className="space-y-3">
          {agent.steps.map((step, index) => (
            <li key={step.text} className="flex items-center gap-3">
              <span className="mono flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border bg-raised text-sm text-foreground">
                {index + 1}
              </span>
              <span className="flex flex-wrap items-center gap-2">
                {step.text}
                {step.code && <Code>{step.code}</Code>}
              </span>
            </li>
          ))}
        </ol>
        <CodeBlock tabs={agent.config} lineNumbers />
        {agent.notes && (
          <p className="flex gap-2 rounded-lg border border-border bg-card p-3 text-sm text-muted-foreground">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" /> {agent.notes}
          </p>
        )}
      </div>

      <h3 className="pt-2 text-xl font-semibold text-foreground">Environment variables</h3>
      <p className="text-sm text-muted-foreground">Quick reference for every supported agent. Values point at Rev9 Apis.</p>
      <Table
        head={["Agent", "Variable", "Value"]}
        rows={[
          ["Claude Code", <Code key="v">ANTHROPIC_BASE_URL</Code>, <span key="u" className="mono text-[13px]">{BASE}</span>],
          ["Claude Code", <Code key="v">ANTHROPIC_AUTH_TOKEN</Code>, <span key="u" className="mono text-[13px]">mgw_…</span>],
          ["Codex CLI", <Code key="v">base_url (config.toml)</Code>, <span key="u" className="mono text-[13px]">{V1}</span>],
          ["OpenHands", <Code key="v">LLM_BASE_URL</Code>, <span key="u" className="mono text-[13px]">{V1}</span>],
          ["Hermes Agent", <Code key="v">OPENAI_BASE_URL</Code>, <span key="u" className="mono text-[13px]">{V1}</span>],
          ["Cursor / Cline", <Code key="v">Base URL</Code>, <span key="u" className="mono text-[13px]">{V1}</span>],
          ...MORE_AGENTS.map((item) => [
            item.name,
            <Code key="v">{item.variable}</Code>,
            <span key="u" className="mono text-[13px]">{item.value}</span>,
          ]),
        ]}
      />
    </Section>
  );
}

type Protocol = "chat" | "responses" | "messages";

function Playground() {
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: ({ signal }) => fetchModels(signal) });
  const textModels = useMemo(() => (modelsQuery.data?.models ?? []).filter((model) => model.type === "text"), [modelsQuery.data]);
  const [protocol, setProtocol] = useState<Protocol>("chat");
  const [model, setModel] = useState<string>("");
  const [apiKey, setApiKey] = useState<string>("");
  const [stream, setStream] = useState<boolean>(true);
  const [prompt, setPrompt] = useState<string>("Explain speculative decoding in 3 bullets.");
  const [output, setOutput] = useState<string>("");
  const [meta, setMeta] = useState<string>("");
  const [running, setRunning] = useState<boolean>(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!model && textModels.length) setModel(textModels[0].id);
  }, [model, textModels]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async () => {
    const key = apiKey.trim();
    if (!/^mgw_[A-Za-z0-9_-]{8,}$/.test(key)) {
      setOutput("Enter a valid gateway key (starts with mgw_).");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setOutput("");
    setMeta("");
    const started = performance.now();
    let first = 0;
    const path = protocol === "chat" ? "/v1/chat/completions" : protocol === "responses" ? "/v1/responses" : "/v1/messages";
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    let body: Record<string, unknown>;
    if (protocol === "messages") {
      headers["x-api-key"] = key;
      headers["anthropic-version"] = "2023-06-01";
      body = { model, max_tokens: 1024, stream, messages: [{ role: "user", content: prompt }] };
    } else if (protocol === "responses") {
      headers.Authorization = `Bearer ${key}`;
      body = { model, input: prompt, stream };
    } else {
      headers.Authorization = `Bearer ${key}`;
      body = { model, stream, messages: [{ role: "user", content: prompt }] };
    }
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
        credentials: "omit",
        referrerPolicy: "no-referrer",
      });
      if (!response.ok || !stream || !response.body) {
        const text = await response.text();
        first = performance.now() - started;
        try {
          const json = JSON.parse(text) as Record<string, any>;
          const content =
            json.error?.message ??
            json.choices?.[0]?.message?.content ??
            json.output_text ??
            json.content?.map((block: { text?: string }) => block.text ?? "").join("") ??
            text;
          setOutput(String(content));
        } catch {
          setOutput(text.slice(0, 4000));
        }
        setMeta(`HTTP ${response.status} · ${Math.round(performance.now() - started)} ms`);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let chunks = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === "[DONE]") continue;
          try {
            const event = JSON.parse(payload) as Record<string, any>;
            const delta: string =
              event.choices?.[0]?.delta?.content ??
              (event.type === "response.output_text.delta" ? event.delta : undefined) ??
              (event.type === "content_block_delta" ? event.delta?.text : undefined) ??
              event.error?.message ??
              "";
            if (delta) {
              if (!first) first = performance.now() - started;
              chunks += 1;
              setOutput((prev) => prev + delta);
            }
          } catch {
            /* ignore keep-alive or partial lines */
          }
        }
      }
      setMeta(`HTTP ${response.status} · TTFT ${Math.round(first)} ms · ${Math.round(performance.now() - started)} ms · ${chunks} chunks`);
    } catch (error) {
      if ((error as Error).name !== "AbortError") setOutput("Network error — check your connection and try again.");
    } finally {
      setRunning(false);
    }
  }, [apiKey, model, prompt, protocol, stream]);

  const inputClass = "h-10 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-[#5f5f66]";

  return (
    <Section id="playground" eyebrow="Interactive" title="Live Playground">
      <p>Send a real request through the gateway. Your key stays in this tab's memory only and is never stored.</p>
      <div className="panel space-y-4 p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1.5 block text-muted-foreground">Protocol</span>
            <select value={protocol} onChange={(event) => setProtocol(event.target.value as Protocol)} className={inputClass}>
              <option value="chat">OpenAI · /v1/chat/completions</option>
              <option value="responses">OpenAI Responses · /v1/responses</option>
              <option value="messages">Anthropic · /v1/messages</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-muted-foreground">Model</span>
            <select value={model} onChange={(event) => setModel(event.target.value)} className={inputClass}>
              {textModels.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                  {item.context_window ? ` · ${fmtTokens(item.context_window)}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-muted-foreground">API key</span>
            <input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="mgw_…"
              autoComplete="off"
              spellCheck={false}
              maxLength={200}
              className={cn(inputClass, "mono")}
            />
          </label>
          <fieldset className="text-sm">
            <legend className="mb-1.5 text-muted-foreground">Mode</legend>
            <div className="flex h-10 rounded-lg border border-border bg-background p-1">
              {[true, false].map((value) => (
                <button
                  key={String(value)}
                  type="button"
                  aria-pressed={stream === value}
                  onClick={() => setStream(value)}
                  className={cn("flex-1 rounded-md text-sm transition", stream === value ? "bg-raised" : "text-muted-foreground")}
                >
                  {value ? "Stream (SSE)" : "JSON"}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
        <label className="block text-sm">
          <span className="mb-1.5 block text-muted-foreground">Prompt</span>
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={3}
            maxLength={8000}
            className="w-full resize-y rounded-lg border border-border bg-background p-3 text-sm outline-none focus:border-[#5f5f66]"
          />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          {running ? (
            <button type="button" onClick={() => abortRef.current?.abort()} className="btn-secondary">
              <Square className="h-4 w-4" /> Stop
            </button>
          ) : (
            <button type="button" onClick={run} disabled={!model || !prompt.trim()} className="btn-primary">
              <Play className="h-4 w-4" /> Send request
            </button>
          )}
          {running && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          <span className="mono text-xs text-muted-foreground">{meta}</span>
        </div>
        <div className="relative">
          <pre
            aria-live="polite"
            className="mono min-h-[120px] max-h-[360px] overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-[#0D0D0F] p-4 text-[13px] leading-6 text-[#D6D6D2]"
          >
            {output || "Response output appears here."}
          </pre>
          {output && <CopyButton value={output} label="Copy output" className="absolute right-2 top-2" />}
        </div>
      </div>
    </Section>
  );
}

function useActiveSection(ids: string[]): string {
  const [active, setActive] = useState<string>(ids[0]);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-80px 0px -70% 0px" },
    );
    ids.forEach((id) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, [ids]);
  return active;
}

const SECTION_CODE: Record<string, { title: string; tabs: typeof QUICKSTART }> = {
  overview: { title: "Quickstart", tabs: QUICKSTART },
  quickstart: { title: "Quickstart", tabs: QUICKSTART },
  authentication: { title: "Authentication", tabs: AUTH_CODE },
  "dual-protocol": { title: "Quickstart", tabs: QUICKSTART },
  "tool-calling": { title: "Tools", tabs: TOOLS_CODE },
  "json-mode": { title: "JSON mode", tabs: JSON_CODE },
  streaming: { title: "Streaming", tabs: STREAM_CODE },
  "responses-api": { title: "Responses", tabs: RESPONSES_CODE },
  errors: { title: "Retry", tabs: RETRY_CODE },
  retries: { title: "Retry", tabs: RETRY_CODE },
};

const Docs = () => {
  const ids = useMemo(() => TOC.flatMap((group) => group.items.map((item) => item.id)), []);
  const active = useActiveSection(ids);
  const agentConfig = AGENTS[0].config;
  const panel = SECTION_CODE[active] ?? (active === "agents" ? { title: "Agent", tabs: agentConfig } : SECTION_CODE.quickstart);

  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash) window.setTimeout(() => document.getElementById(hash)?.scrollIntoView(), 50);
  }, []);

  return (
    <SiteLayout>
      <div className="mx-auto grid max-w-[1400px] lg:grid-cols-[250px_minmax(0,1fr)] xl:grid-cols-[250px_minmax(0,1fr)_420px]">
        <aside className="hidden border-r border-border lg:block">
          <nav aria-label="Documentation" className="sticky top-16 max-h-[calc(100vh-4rem)] overflow-y-auto py-8 pr-4">
            {TOC.map((group) => (
              <div key={group.title} className="mb-6">
                <p className="px-6 pb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">{group.title}</p>
                {group.items.map((item) => (
                  <a
                    key={item.id}
                    href={`#${item.id}`}
                    aria-current={active === item.id ? "location" : undefined}
                    className={cn(
                      "block border-l-2 py-2.5 pl-6 pr-3 text-sm transition",
                      active === item.id
                        ? "border-ok bg-white/[0.04] font-semibold text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {item.label}
                  </a>
                ))}
              </div>
            ))}
          </nav>
        </aside>

        <article className="min-w-0 space-y-12 px-4 py-10 sm:px-8 lg:px-10">
          <details className="panel p-3 lg:hidden">
            <summary className="cursor-pointer text-sm font-medium">On this page</summary>
            <nav aria-label="Documentation (mobile)" className="mt-2 grid gap-1">
              {TOC.flatMap((group) => group.items).map((item) => (
                <a key={item.id} href={`#${item.id}`} className="rounded px-2 py-1.5 text-sm text-muted-foreground hover:text-foreground">
                  {item.label}
                </a>
              ))}
            </nav>
          </details>

          <section id="overview" aria-labelledby="overview-title">
            <p className="mono text-xs font-semibold uppercase tracking-[0.12em] text-ok">Docs</p>
            <h1 id="overview-title" className="mt-2 text-4xl font-bold tracking-tight sm:text-5xl">
              High-Performance Inference Edge Gateway
            </h1>
            <p className="mt-4 text-lg leading-8 text-[#C9C9C5]">
              Rev9 Apis routes one endpoint to frontier models. It works as a drop-in with the official OpenAI and Anthropic SDKs, Claude
              Code, Codex, OpenHands, Hermes, Cursor and Cline, so you don't rewrite any application code.
            </p>
            <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {[
                ["1. Client request", "OpenAI SDK, Anthropic SDK or coding agent"],
                ["2. Edge auth", "Hashed key check · rate limits · size limits"],
                ["3. Dual protocol", "Chat Completions, Responses and Messages"],
                ["4. Model fleet", "Streams back with metered usage"],
              ].map(([title, body]) => (
                <div key={title} className="panel p-4">
                  <p className="text-sm font-semibold">{title}</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">{body}</p>
                </div>
              ))}
            </div>
            <div className="mt-6 flex flex-col gap-2 rounded-[10px] border border-border bg-card p-3 sm:flex-row sm:items-center">
              <span className="text-sm text-muted-foreground">Base URL</span>
              <code className="mono flex-1 text-sm">{V1}</code>
              <CopyButton value={V1} label="Copy base URL" />
            </div>
          </section>

          <Section id="quickstart" eyebrow="Getting started" title="60-Second Quickstart">
            <p>Point an official client library at the gateway. Any model enabled on your key works by changing the <Code>model</Code> field.</p>
            <div className="xl:hidden">
              <CodeBlock tabs={QUICKSTART} />
            </div>
          </Section>

          <Section id="authentication" eyebrow="Getting started" title="Authentication & Keys">
            <p>
              Every call needs a gateway key starting with <Code>mgw_</Code>. Keys are generated in the Rev9 control center, shown once and
              stored only as a salted hash, so lost keys must be rotated, not recovered.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="panel p-4">
                <p className="font-semibold text-foreground">OpenAI header</p>
                <p className="mt-1 text-sm text-muted-foreground">Chat Completions, Responses, models and media.</p>
                <code className="inline-code mt-3 block w-fit">Authorization: Bearer mgw_…</code>
              </div>
              <div className="panel p-4">
                <p className="font-semibold text-foreground">Anthropic header</p>
                <p className="mt-1 text-sm text-muted-foreground">Messages endpoint, plus anthropic-version.</p>
                <code className="inline-code mt-3 block w-fit">x-api-key: mgw_…</code>
              </div>
            </div>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              <li>Never put keys in browser code, mobile bundles or public repos. Call the gateway from your server.</li>
              <li>Each key has a per-minute rate limit. Repeated invalid keys from one address are temporarily blocked.</li>
              <li>Rotating a key invalidates the old one immediately.</li>
            </ul>
            <div className="xl:hidden">
              <CodeBlock tabs={AUTH_CODE} />
            </div>
          </Section>

          <Section id="dual-protocol" eyebrow="Protocols" title="OpenAI & Anthropic Dual Protocol">
            <p>
              You never need to rewrite a client. The gateway exposes native endpoints for both the OpenAI Chat Completions standard and the
              Anthropic Messages specification on the same key.
            </p>
            <Table
              head={["Property", "OpenAI-compatible", "Anthropic-compatible"]}
              rows={[
                ["Endpoint", <Code key="a">POST {V1}/chat/completions</Code>, <Code key="b">POST {V1}/messages</Code>],
                ["Auth header", <Code key="a">Authorization: Bearer mgw_…</Code>, <Code key="b">x-api-key: mgw_…</Code>],
                ["Version header", "Not required", <Code key="b">anthropic-version: 2023-06-01</Code>],
                ["System prompt", <Code key="a">{'{"role": "system", ...}'}</Code>, <Code key="b">{'top-level "system"'}</Code>],
                ["Tools", <Code key="a">tools[].function</Code>, <Code key="b">tools[].input_schema</Code>],
                ["Streaming", <Code key="a">{'data: {"choices":[{"delta"}]}'}</Code>, <Code key="b">event: content_block_delta</Code>],
                ["Max tokens", "Optional", "Required (positive integer)"],
              ]}
            />
          </Section>

          <Section id="tool-calling" eyebrow="Protocols" title="Tool Calling">
            <p>
              Define tools in the format your client already uses. OpenAI clients send <Code>tools[].function.parameters</Code>, Anthropic
              clients send <Code>tools[].input_schema</Code>. Tool results go back as <Code>role: "tool"</Code> messages (OpenAI) or{" "}
              <Code>tool_result</Code> content blocks (Anthropic).
            </p>
            <CodeBlock tabs={TOOLS_CODE} className="xl:hidden" />
          </Section>

          <Section id="json-mode" eyebrow="Protocols" title="Structured JSON Mode">
            <p>
              Ask for parseable JSON with <Code>{'response_format: {"type": "json_object"}'}</Code> in OpenAI mode, or describe the schema in
              the system prompt in Anthropic mode. Always validate the parsed result in your code.
            </p>
            <CodeBlock tabs={JSON_CODE} className="xl:hidden" />
          </Section>

          <Section id="streaming" eyebrow="Protocols" title="Streaming (SSE)">
            <p>
              Set <Code>stream: true</Code> to receive Server-Sent Events with no buffering. On Chat Completions the gateway adds{" "}
              <Code>stream_options.include_usage</Code> automatically, so the final chunk carries token usage. Long generations should always
              stream to avoid timeouts.
            </p>
            <CodeBlock tabs={STREAM_CODE} className="xl:hidden" />
          </Section>

          <Section id="responses-api" eyebrow="Protocols" title="OpenAI Responses API">
            <p>
              <Code>POST /v1/responses</Code> unifies chat, tools and reasoning into an agent-oriented schema. It takes a single{" "}
              <Code>input</Code>, top-level <Code>instructions</Code>, and returns structured output items. Codex CLI uses this route.
            </p>
            <Table
              head={["Field", "Type", "Required", "Description"]}
              rows={RESPONSES_PARAMS.map((param) => [
                <Code key="f">{param.field}</Code>,
                <span key="t" className="mono text-xs">{param.type}</span>,
                param.req,
                param.desc,
              ])}
            />
            <CodeBlock tabs={RESPONSES_CODE} className="xl:hidden" />
          </Section>

          <AgentSection />

          <Section id="sdks" eyebrow="Integrations" title="Client SDKs">
            <p>Drop the gateway URL into the SDK you already use. You don't need any wrapper libraries.</p>
            <div className="space-y-6">
              {SDKS.map((sdk) => (
                <div key={sdk.title}>
                  <div className="mb-2 flex flex-wrap items-center gap-3">
                    <h3 className="font-semibold text-foreground">{sdk.title}</h3>
                    <Code>{sdk.install}</Code>
                  </div>
                  <CodeBlock tabs={[sdk.tab]} title={sdk.tab.label} />
                </div>
              ))}
            </div>
          </Section>

          <Section id="api-reference" eyebrow="Reference" title="API Reference">
            <p>
              All routes live under <Code>{V1}</Code>. Model IDs are case-sensitive and must be enabled for your key.
            </p>
            <div className="space-y-6">
              {ENDPOINTS.map((endpoint) => (
                <div key={endpoint.path} className="panel overflow-hidden">
                  <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
                    <span
                      className={cn(
                        "mono rounded-md px-2 py-0.5 text-xs font-semibold",
                        endpoint.method === "GET" ? "bg-info/15 text-info" : "bg-ok/10 text-ok",
                      )}
                    >
                      {endpoint.method}
                    </span>
                    <code className="mono text-sm font-medium text-foreground">{endpoint.path}</code>
                  </div>
                  <p className="px-4 py-3 text-sm">{endpoint.desc}</p>
                  <CodeBlock tabs={[{ label: "cURL", code: endpoint.code }]} title="cURL" className="rounded-none border-x-0 border-b-0" />
                </div>
              ))}
            </div>
          </Section>

          <Playground />

          <Section id="errors" eyebrow="Reference" title="Errors & rate limits">
            <p>Errors use the OpenAI-style envelope on every route, so standard SDK error handling just works. Upstream details are never exposed.</p>
            <CodeBlock tabs={[{ label: "json", code: ERROR_ENVELOPE }]} title="Error envelope" />
            <Table
              head={["Status", "Code", "Cause", "Fix"]}
              rows={ERRORS.map((error) => [
                <span
                  key="s"
                  className={cn("mono text-sm font-semibold", error.status >= 500 ? "text-err" : error.status === 429 ? "text-warn" : "text-foreground")}
                >
                  {error.status}
                </span>,
                <Code key="c">{error.code}</Code>,
                error.cause,
                error.fix,
              ])}
            />
          </Section>

          <Section id="retries" eyebrow="Reference" title="Retry & backoff">
            <p>Retry 429, 502, 503 and 504 with exponential backoff and jitter. Don't retry 400, 401 or 403. Fix the request instead.</p>
            <CodeBlock tabs={RETRY_CODE} className="xl:hidden" />
          </Section>
        </article>

        <aside className="hidden border-l border-border xl:block" aria-label="Code samples">
          <div className="sticky top-16 p-6">
            <p className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
              {active === "agents" ? <ModelLogo modelId="claude" size={18} /> : null}
              {panel.title}
            </p>
            <CodeBlock key={active} tabs={panel.tabs} lineNumbers maxHeight={640} />
          </div>
        </aside>
      </div>
    </SiteLayout>
  );
};

export default Docs;
