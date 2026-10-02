import type { GatewayModel, ModelType } from "@/lib/api";
import { fmtInt, fmtTokens } from "@/lib/format";

export interface ModelFamily {
  key: string;
  label: string;
  logo: string | null;
  mark: string;
  tint: string;
}

const FAMILIES: { match: RegExp; family: ModelFamily }[] = [
  { match: /^(gpt|o\d|chatgpt)/i, family: { key: "openai", label: "OpenAI", logo: "/logos/openai.svg", mark: "O", tint: "#FFFFFF" } },
  { match: /^claude/i, family: { key: "anthropic", label: "Claude", logo: "/logos/claude.svg", mark: "C", tint: "#D97757" } },
  { match: /^grok/i, family: { key: "xai", label: "Grok", logo: "/logos/grok.svg", mark: "X", tint: "#FFFFFF" } },
  { match: /^gemini/i, family: { key: "google", label: "Gemini", logo: "/logos/gemini.svg", mark: "G", tint: "#3186FF" } },
  { match: /^deepseek/i, family: { key: "deepseek", label: "DeepSeek", logo: "/logos/deepseek.svg", mark: "D", tint: "#4D6BFE" } },
  { match: /^kimi/i, family: { key: "kimi", label: "Kimi", logo: "/logos/kimi.svg", mark: "K", tint: "#1783FF" } },
  { match: /^qwen/i, family: { key: "qwen", label: "Qwen", logo: "/logos/qwen.svg", mark: "Q", tint: "#615CED" } },
  { match: /^glm/i, family: { key: "glm", label: "GLM", logo: "/logos/zhipu.svg", mark: "Z", tint: "#3859FF" } },
  { match: /^minimax/i, family: { key: "minimax", label: "MiniMax", logo: "/logos/minimax.svg", mark: "M", tint: "#E2167E" } },
  { match: /^step/i, family: { key: "stepfun", label: "StepFun", logo: "/logos/stepfun.svg", mark: "S", tint: "#01A9FF" } },
  { match: /^kira/i, family: { key: "kira", label: "Kira", logo: null, mark: "K", tint: "#F5B544" } },
];

const FALLBACK: ModelFamily = { key: "other", label: "Other", logo: null, mark: "•", tint: "#8E8E93" };

export const FAMILY_ORDER = ["openai", "anthropic", "xai", "google", "deepseek", "kimi", "qwen", "glm", "minimax", "stepfun", "kira", "other"];

export function familyOf(modelId: string): ModelFamily {
  return FAMILIES.find((entry) => entry.match.test(modelId))?.family ?? { ...FALLBACK, mark: modelId.charAt(0).toUpperCase() };
}

export const ALL_FAMILIES: ModelFamily[] = FAMILIES.map((entry) => entry.family);

export const TYPE_LABEL: Record<ModelType, string> = {
  text: "Text",
  image: "Image",
  video: "Video",
  speech: "Speech",
};

/** Capability tags shown on model cards. */
export function capabilityTags(model: GatewayModel): string[] {
  if (model.type !== "text") return [TYPE_LABEL[model.type]];
  const id = model.id.toLowerCase();
  const tags = ["Text"];
  if (/claude|gpt|gemini|grok|kimi-k3|minimax|kira/.test(id)) tags.push("Vision");
  if (!/image|video|tts/.test(id)) tags.push("Tools");
  if (/code|coder/.test(id)) tags.push("Code");
  return tags;
}

export function contextLabel(model: GatewayModel): string {
  if (model.context_window) return fmtTokens(model.context_window);
  return model.type === "text" ? "Provider max" : "Prompt only";
}

export function maxOutputLabel(model: GatewayModel): string {
  if (model.max_output_tokens) return fmtTokens(model.max_output_tokens);
  if (model.type === "image") return "1 image";
  if (model.type === "video") return "1 video";
  if (model.type === "speech") return "Audio";
  return model.observed.max_output_tokens ? `${fmtInt(model.observed.max_output_tokens)} seen` : "Provider max";
}

/** Observed generation speed; falls back to end-to-end throughput for older telemetry. */
export function speedLabel(model: GatewayModel): string {
  const observed = model.observed;
  if (model.type !== "text") return model.type === "speech" ? "Streamed audio" : "Async job";
  if (observed.tokens_per_second) return `${fmtInt(observed.tokens_per_second)} tok/s`;
  if (observed.avg_output_tokens && observed.avg_latency_ms && observed.avg_output_tokens > 1) {
    const value = observed.avg_output_tokens / (observed.avg_latency_ms / 1000);
    return `≈${value >= 10 ? fmtInt(value) : value.toFixed(1)} tok/s`;
  }
  return "Streaming";
}

export function latencyLabel(model: GatewayModel): string {
  return model.observed.avg_latency_ms ? `${fmtInt(model.observed.avg_latency_ms)} ms` : "Awaiting traffic";
}
