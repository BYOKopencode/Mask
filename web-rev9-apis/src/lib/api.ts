import snapshot from "@/data/models-snapshot.json";

/**
 * Base URL of the gateway. When the site is served by the gateway itself
 * (api.yuvraj.pro) requests stay same-origin; previews call the live host.
 */
const PREVIEW_HOST = /(^localhost$)|rork|127\.0\.0\.1/;
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  (typeof window !== "undefined" && PREVIEW_HOST.test(window.location.hostname) ? "https://api.yuvraj.pro" : "");

export const PUBLIC_BASE_URL = "https://api.yuvraj.pro";

export type ModelType = "text" | "image" | "video" | "speech";

export interface ModelObserved {
  requests: number;
  total_tokens: number;
  avg_latency_ms: number | null;
  avg_ttft_ms: number | null;
  avg_output_tokens: number | null;
  max_output_tokens: number | null;
  success_rate: number | null;
  tokens_per_second: number | null;
  last_observed: string | null;
}

export interface GatewayModel {
  id: string;
  type: ModelType;
  enabled: boolean;
  context_window: number | null;
  max_output_tokens: number | null;
  observed: ModelObserved;
}

export interface ModelsResponse {
  models: GatewayModel[];
  source: "live" | "snapshot";
}

export interface PublicStats {
  tokens_processed: number;
  requests: number;
  tokens_per_minute: number;
  requests_per_minute: number;
  models: number;
  routes_online: number;
  routes_healthy: number;
  series: { requests: number[]; tokens: number[] };
  generated_at: string;
  source: "live" | "snapshot";
}

export interface RequestRow {
  id: string;
  model: string;
  api: string;
  status: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  latency_ms: number;
  ttft_ms: number | null;
  streamed: boolean;
  tokens_per_second: number | null;
  created_at: string;
}

export interface RequestsResponse {
  page: number;
  size: number;
  total: number;
  summary: {
    requests: number;
    tokens: number;
    avg_latency_ms: number | null;
    avg_ttft_ms: number | null;
    success_rate: number | null;
  };
  models: string[];
  requests: RequestRow[];
}

export type StatusFilter = "all" | "2xx" | "4xx" | "5xx";
export type RangeFilter = "1h" | "24h" | "7d" | "30d";
export type SortKey = "time" | "latency" | "input" | "output" | "ttft" | "status" | "model";

export interface RequestQuery {
  page: number;
  size: number;
  model: string;
  status: StatusFilter;
  range: RangeFilter;
  q: string;
  sort: SortKey;
  order: "asc" | "desc";
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    signal,
    credentials: "omit",
    headers: { Accept: "application/json" },
    referrerPolicy: "no-referrer",
  });
  if (!response.ok) {
    throw new Error(response.status === 429 ? "Too many requests — slow down a little." : `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

const snapshotModels = (snapshot as { models: GatewayModel[] }).models;

/** Enabled models with limits and observed telemetry; falls back to a bundled snapshot. */
export async function fetchModels(signal?: AbortSignal): Promise<ModelsResponse> {
  try {
    const data = await getJson<{ models: GatewayModel[] }>("/models/public", signal);
    const models = data.models.map((model) => ({
      ...model,
      type: model.type ?? "text",
      context_window: typeof model.context_window === "number" ? model.context_window : null,
      max_output_tokens: typeof model.max_output_tokens === "number" ? model.max_output_tokens : null,
    }));
    const hasLimits = models.some((model) => model.context_window !== null);
    if (!hasLimits) {
      // Older gateway builds report no limits; merge in known limits from the snapshot.
      const known = new Map(snapshotModels.map((model) => [model.id, model]));
      return {
        source: "live",
        models: models.map((model) => ({
          ...model,
          type: known.get(model.id)?.type ?? model.type,
          context_window: known.get(model.id)?.context_window ?? null,
          max_output_tokens: known.get(model.id)?.max_output_tokens ?? null,
        })),
      };
    }
    return { models, source: "live" };
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    return { models: snapshotModels, source: "snapshot" };
  }
}

/** Landing page counters; falls back to totals derived from the model snapshot. */
export async function fetchStats(signal?: AbortSignal): Promise<PublicStats> {
  try {
    const data = await getJson<Omit<PublicStats, "source">>("/stats/public", signal);
    return { ...data, source: "live" };
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    const requests = snapshotModels.reduce((sum, model) => sum + model.observed.requests, 0);
    const tokens = snapshotModels.reduce(
      (sum, model) => sum + Math.round((model.observed.avg_output_tokens ?? 0) * model.observed.requests),
      0,
    );
    return {
      tokens_processed: tokens,
      requests,
      tokens_per_minute: 0,
      requests_per_minute: 0,
      models: snapshotModels.length,
      routes_online: 0,
      routes_healthy: 0,
      series: { requests: [], tokens: [] },
      generated_at: new Date().toISOString(),
      source: "snapshot",
    };
  }
}

function requestParams(query: RequestQuery): URLSearchParams {
  const params = new URLSearchParams({
    page: String(query.page),
    size: String(query.size),
    status: query.status,
    range: query.range,
    sort: query.sort,
    order: query.order,
  });
  if (query.model) params.set("model", query.model);
  if (query.q.trim()) params.set("q", query.q.trim());
  return params;
}

/** Request telemetry only — the gateway never returns prompts or responses here. */
export function fetchRequests(query: RequestQuery, signal?: AbortSignal): Promise<RequestsResponse> {
  return getJson<RequestsResponse>(`/requests/public?${requestParams(query).toString()}`, signal);
}

export function requestsCsvUrl(query: RequestQuery): string {
  const params = requestParams(query);
  params.delete("page");
  params.delete("size");
  params.set("format", "csv");
  return `${API_BASE}/requests/public?${params.toString()}`;
}
