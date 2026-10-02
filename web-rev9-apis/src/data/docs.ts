import type { CodeTab } from "@/components/site/CodeBlock";

export const BASE = "https://api.yuvraj.pro";
export const V1 = `${BASE}/v1`;
export const KEY = "mgw_XX...";
export const CODE_MODEL = "Kimi-K2.7-Code";
export const CHAT_MODEL = "claude-opus-4-6";

export interface TocGroup {
  title: string;
  items: { id: string; label: string }[];
}

export const TOC: TocGroup[] = [
  {
    title: "Getting started",
    items: [
      { id: "overview", label: "Edge Inference Gateway" },
      { id: "quickstart", label: "60-Second Quickstart" },
      { id: "authentication", label: "Authentication & Keys" },
    ],
  },
  {
    title: "Protocols",
    items: [
      { id: "dual-protocol", label: "OpenAI & Anthropic Dual Protocol" },
      { id: "tool-calling", label: "Tool Calling" },
      { id: "json-mode", label: "Structured JSON Mode" },
      { id: "streaming", label: "Streaming (SSE)" },
      { id: "responses-api", label: "OpenAI Responses API" },
    ],
  },
  {
    title: "Integrations",
    items: [
      { id: "agents", label: "Agent API Usage" },
      { id: "sdks", label: "Client SDKs" },
    ],
  },
  {
    title: "Reference",
    items: [
      { id: "api-reference", label: "API Reference" },
      { id: "playground", label: "Live Playground" },
      { id: "errors", label: "Errors & rate limits" },
      { id: "retries", label: "Retry & backoff" },
    ],
  },
];

export const QUICKSTART: CodeTab[] = [
  {
    label: "Python",
    code: `from openai import OpenAI

client = OpenAI(
    base_url="${V1}",
    api_key="${KEY}",
)

stream = client.chat.completions.create(
    model="${CHAT_MODEL}",
    messages=[
        {"role": "system", "content": "You are an expert AI research assistant."},
        {"role": "user", "content": "Explain multi-head latent attention in 3 sentences."},
    ],
    stream=True,
)

for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)`,
  },
  {
    label: "TypeScript",
    code: `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "${V1}",
  apiKey: process.env.REV9_API_KEY, // ${KEY}
});

const stream = await client.chat.completions.create({
  model: "${CHAT_MODEL}",
  messages: [{ role: "user", content: "Write a haiku about latency." }],
  stream: true,
});

for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}`,
  },
  {
    label: "cURL",
    code: `curl ${V1}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${KEY}" \\
  -d '{
    "model": "${CHAT_MODEL}",
    "messages": [{"role": "user", "content": "Hello from Rev9!"}],
    "stream": false
  }'`,
  },
  {
    label: "Anthropic SDK",
    code: `import anthropic

client = anthropic.Anthropic(
    base_url="${BASE}",
    api_key="${KEY}",
)

message = client.messages.create(
    model="${CHAT_MODEL}",
    max_tokens=1024,
    system="You are a senior systems architect.",
    messages=[{"role": "user", "content": "Explain Raft quorum requirements."}],
)
print(message.content[0].text)`,
  },
];

export const AUTH_CODE: CodeTab[] = [
  {
    label: "Headers",
    code: `# OpenAI-style clients (chat/completions, responses, models, media)
Authorization: Bearer ${KEY}

# Anthropic-style clients (messages)
x-api-key: ${KEY}
anthropic-version: 2023-06-01`,
  },
  {
    label: ".env",
    code: `# Never commit keys or ship them in browser code
REV9_API_KEY=${KEY}
OPENAI_BASE_URL=${V1}
ANTHROPIC_BASE_URL=${BASE}`,
  },
];

export const TOOLS_CODE: CodeTab[] = [
  {
    label: "OpenAI tools",
    code: `{
  "model": "${CODE_MODEL}",
  "messages": [{"role": "user", "content": "Price of NVDA?"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_stock_quote",
      "description": "Fetch real-time equity pricing",
      "parameters": {
        "type": "object",
        "properties": {
          "ticker": {"type": "string", "description": "e.g. AAPL, NVDA"}
        },
        "required": ["ticker"]
      }
    }
  }]
}`,
  },
  {
    label: "Anthropic tools",
    code: `{
  "model": "${CODE_MODEL}",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "Price of NVDA?"}],
  "tools": [{
    "name": "get_stock_quote",
    "description": "Fetch real-time equity pricing",
    "input_schema": {
      "type": "object",
      "properties": {
        "ticker": {"type": "string", "description": "e.g. AAPL, NVDA"}
      },
      "required": ["ticker"]
    }
  }]
}`,
  },
];

export const JSON_CODE: CodeTab[] = [
  {
    label: "Python",
    code: `response = client.chat.completions.create(
    model="${CHAT_MODEL}",
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": "Reply only with JSON: {\\"city\\": str, \\"country\\": str}"},
        {"role": "user", "content": "Where is the Eiffel Tower?"},
    ],
)
data = json.loads(response.choices[0].message.content)`,
  },
];

export const STREAM_CODE: CodeTab[] = [
  {
    label: "OpenAI SSE",
    code: `data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant"}}]}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"Hello"}}]}

data: {"id":"chatcmpl-1","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":9}}

data: [DONE]`,
  },
  {
    label: "Anthropic SSE",
    code: `event: message_start
data: {"type":"message_start","message":{"usage":{"input_tokens":12}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: message_delta
data: {"type":"message_delta","usage":{"output_tokens":9}}

event: message_stop
data: {"type":"message_stop"}`,
  },
];

export const RESPONSES_CODE: CodeTab[] = [
  {
    label: "Python",
    code: `from openai import OpenAI

client = OpenAI(base_url="${V1}", api_key="${KEY}")

response = client.responses.create(
    model="${CODE_MODEL}",
    instructions="You are a principal distributed systems architect.",
    input="Design an idempotent payment lock with Redis and PostgreSQL fallback.",
)
print(response.output_text)

# Streaming
stream = client.responses.create(model="${CODE_MODEL}", input="Explain Raft.", stream=True)
for event in stream:
    if event.type == "response.output_text.delta":
        print(event.delta, end="", flush=True)`,
  },
  {
    label: "TypeScript",
    code: `import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${V1}", apiKey: process.env.REV9_API_KEY });

const response = await client.responses.create({
  model: "${CODE_MODEL}",
  instructions: "You are an elite code reviewer.",
  input: "Find race conditions in this async Rust state machine.",
});
console.log(response.output_text);`,
  },
  {
    label: "cURL",
    code: `curl ${V1}/responses \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${KEY}" \\
  -d '{
    "model": "${CODE_MODEL}",
    "instructions": "Be direct and concise.",
    "input": "Explain speculative decoding in 3 bullets.",
    "stream": false
  }'`,
  },
];

export const RESPONSES_PARAMS: { field: string; type: string; req: string; desc: string }[] = [
  { field: "model", type: "string", req: "Required", desc: "A model enabled on your key, e.g. Kimi-K2.7-Code." },
  { field: "input", type: "string | array", req: "Required", desc: "Prompt string or array of input items (messages, input_text, function_call_output)." },
  { field: "instructions", type: "string", req: "Optional", desc: "System-level instructions with the highest steering priority." },
  { field: "stream", type: "boolean", req: "Optional", desc: "Stream SSE events (response.output_text.delta, response.completed)." },
  { field: "max_output_tokens", type: "integer", req: "Optional", desc: "Generation cap, bounded by the model's max output." },
  { field: "temperature", type: "number", req: "Optional", desc: "Sampling temperature, 0.0–2.0." },
  { field: "tools", type: "array", req: "Optional", desc: "Functions the model may call during the agent loop." },
];

export const SDKS: { title: string; install: string; tab: CodeTab }[] = [
  {
    title: "OpenAI Python SDK",
    install: "pip install openai",
    tab: {
      label: "python",
      code: `from openai import OpenAI

client = OpenAI(base_url="${V1}", api_key="${KEY}")
resp = client.chat.completions.create(
    model="Qwen-3.8-Max",
    messages=[{"role": "user", "content": "Prove the sum of the first n odd numbers is n²."}],
    temperature=0.3,
)
print(resp.choices[0].message.content)`,
    },
  },
  {
    title: "OpenAI Node.js / TypeScript SDK",
    install: "npm install openai",
    tab: {
      label: "typescript",
      code: `import OpenAI from "openai";

const openai = new OpenAI({ baseURL: "${V1}", apiKey: process.env.REV9_API_KEY });
const res = await openai.chat.completions.create({
  model: "${CODE_MODEL}",
  messages: [{ role: "user", content: "Generate a React hook for SSE streaming." }],
});
console.log(res.choices[0].message.content);`,
    },
  },
  {
    title: "Anthropic Python SDK",
    install: "pip install anthropic",
    tab: {
      label: "python",
      code: `import anthropic

client = anthropic.Anthropic(base_url="${BASE}", api_key="${KEY}")
msg = client.messages.create(
    model="${CHAT_MODEL}",
    max_tokens=2048,
    messages=[{"role": "user", "content": "Explain consistent hashing."}],
)
print(msg.content[0].text)`,
    },
  },
  {
    title: "Anthropic TypeScript SDK",
    install: "npm install @anthropic-ai/sdk",
    tab: {
      label: "typescript",
      code: `import Anthropic from "@anthropic-ai/sdk";

const anthropic = new Anthropic({ baseURL: "${BASE}", apiKey: process.env.REV9_API_KEY });
const stream = await anthropic.messages.create({
  model: "${CHAT_MODEL}",
  max_tokens: 1024,
  messages: [{ role: "user", content: "Explain token streaming." }],
  stream: true,
});
for await (const event of stream) {
  if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
    process.stdout.write(event.delta.text);
  }
}`,
    },
  },
  {
    title: "Go (go-openai)",
    install: "go get github.com/sashabaranov/go-openai",
    tab: {
      label: "go",
      code: `config := openai.DefaultConfig("${KEY}")
config.BaseURL = "${V1}"
client := openai.NewClientWithConfig(config)

resp, err := client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
    Model:    "DeepSeek-V4.1-Flash",
    Messages: []openai.ChatCompletionMessage{{Role: openai.ChatMessageRoleUser, Content: "Explain Go channels."}},
})
if err != nil { panic(err) }
fmt.Println(resp.Choices[0].Message.Content)`,
    },
  },
];

export interface AgentGuide {
  id: string;
  name: string;
  logo: string | null;
  invert?: boolean;
  protocol: string;
  route: string;
  summary: string;
  steps: { text: string; code?: string }[];
  config: CodeTab[];
  notes?: string;
}

export const AGENTS: AgentGuide[] = [
  {
    id: "claude-code",
    name: "Claude Code",
    logo: "/logos/claude.svg",
    protocol: "Anthropic Messages",
    route: "/v1/messages",
    summary: "Claude Code talks the Anthropic Messages protocol. Point its base URL at the gateway root and it will call /v1/messages.",
    steps: [
      { text: "Install", code: "npm install -g @anthropic-ai/claude-code" },
      { text: "Set ANTHROPIC_BASE_URL to", code: BASE },
      { text: "Set ANTHROPIC_AUTH_TOKEN to your", code: "mgw_" },
      { text: "Pick a model", code: `ANTHROPIC_MODEL=${CODE_MODEL}` },
      { text: "Run in your project folder", code: "claude" },
    ],
    config: [
      {
        label: "Shell",
        code: `export ANTHROPIC_BASE_URL="${BASE}"
export ANTHROPIC_AUTH_TOKEN="${KEY}"
export ANTHROPIC_MODEL="${CODE_MODEL}"
export ANTHROPIC_SMALL_FAST_MODEL="GLM-5.3-Flash"

claude`,
      },
      {
        label: "~/.claude/settings.json",
        code: `{
  "env": {
    "ANTHROPIC_BASE_URL": "${BASE}",
    "ANTHROPIC_AUTH_TOKEN": "${KEY}",
    "ANTHROPIC_MODEL": "${CODE_MODEL}",
    "ANTHROPIC_SMALL_FAST_MODEL": "GLM-5.3-Flash"
  }
}`,
      },
    ],
    notes: "Do not add /v1 to ANTHROPIC_BASE_URL — Claude Code appends /v1/messages itself. ANTHROPIC_API_KEY (x-api-key) works too.",
  },
  {
    id: "codex",
    name: "Codex CLI",
    logo: "/logos/openai.svg",
    invert: true,
    protocol: "OpenAI Responses",
    route: "/v1/responses",
    summary: "Codex CLI uses the Responses API. Register Rev9 as a custom model provider in ~/.codex/config.toml.",
    steps: [
      { text: "Install", code: "npm install -g @openai/codex" },
      { text: "Export your key as", code: "REV9_API_KEY" },
      { text: "Add the rev9 provider to", code: "~/.codex/config.toml" },
      { text: "Set wire_api to", code: "responses" },
      { text: "Run in your project folder", code: "codex" },
    ],
    config: [
      {
        label: "~/.codex/config.toml",
        code: `model = "${CODE_MODEL}"
model_provider = "rev9"

[model_providers.rev9]
name = "Rev9 Apis"
base_url = "${V1}"
env_key = "REV9_API_KEY"
wire_api = "responses"`,
      },
      {
        label: "Shell",
        code: `export REV9_API_KEY="${KEY}"
codex --model ${CODE_MODEL}`,
      },
    ],
  },
  {
    id: "openhands",
    name: "OpenHands",
    logo: null,
    protocol: "OpenAI Chat Completions",
    route: "/v1/chat/completions",
    summary: "OpenHands uses LiteLLM. Prefix the model with openai/ so LiteLLM treats Rev9 as an OpenAI-compatible endpoint.",
    steps: [
      { text: "Open Settings → LLM → Advanced, or set env vars" },
      { text: "Set LLM_BASE_URL to", code: V1 },
      { text: "Set LLM_API_KEY to your", code: "mgw_" },
      { text: "Set the model with the openai/ prefix", code: `openai/${CODE_MODEL}` },
      { text: "Start a conversation in the OpenHands UI or CLI" },
    ],
    config: [
      {
        label: "Shell",
        code: `export LLM_BASE_URL="${V1}"
export LLM_API_KEY="${KEY}"
export LLM_MODEL="openai/${CODE_MODEL}"

openhands`,
      },
      {
        label: "config.toml",
        code: `[llm]
model = "openai/${CODE_MODEL}"
base_url = "${V1}"
api_key = "${KEY}"`,
      },
    ],
  },
  {
    id: "hermes",
    name: "Hermes Agent",
    logo: null,
    protocol: "OpenAI Chat Completions",
    route: "/v1/chat/completions",
    summary: "Hermes Agent supports any OpenAI-compatible endpoint as a custom provider.",
    steps: [
      { text: "Install Hermes Agent and run", code: "hermes setup" },
      { text: "Choose a custom OpenAI-compatible endpoint" },
      { text: "Set the base URL to", code: V1 },
      { text: "Set OPENAI_API_KEY to your", code: "mgw_" },
      { text: "Pick a model", code: CODE_MODEL },
    ],
    config: [
      {
        label: "~/.hermes/config.yaml",
        code: `model:
  provider: custom
  base_url: ${V1}
  default: ${CODE_MODEL}`,
      },
      {
        label: "~/.hermes/.env",
        code: `OPENAI_BASE_URL=${V1}
OPENAI_API_KEY=${KEY}`,
      },
    ],
  },
  {
    id: "cursor",
    name: "Cursor",
    logo: null,
    protocol: "OpenAI Chat Completions",
    route: "/v1/chat/completions",
    summary: "Cursor can override the OpenAI base URL for chat and agent models.",
    steps: [
      { text: "Open Cursor Settings → Models" },
      { text: "Paste your key into the OpenAI API Key field" },
      { text: "Enable Override OpenAI Base URL and enter", code: V1 },
      { text: "Add a custom model", code: CODE_MODEL },
      { text: "Select it in Chat or Agent" },
    ],
    config: [
      {
        label: "Settings",
        code: `OpenAI API Key:        ${KEY}
Override Base URL:     ${V1}
Custom model:          ${CODE_MODEL}`,
      },
    ],
  },
  {
    id: "cline",
    name: "Cline",
    logo: null,
    protocol: "OpenAI Chat Completions",
    route: "/v1/chat/completions",
    summary: "Cline and Roo Code work through their OpenAI Compatible provider.",
    steps: [
      { text: "Open Cline settings and choose API Provider: OpenAI Compatible" },
      { text: "Base URL", code: V1 },
      { text: "API Key", code: "mgw_" },
      { text: "Model ID", code: CODE_MODEL },
      { text: "Save and start a task" },
    ],
    config: [
      {
        label: "Settings",
        code: `API Provider:  OpenAI Compatible
Base URL:      ${V1}
API Key:       ${KEY}
Model ID:      ${CODE_MODEL}`,
      },
    ],
  },
];

export const MORE_AGENTS: { name: string; variable: string; value: string; snippet: string }[] = [
  { name: "Aider", variable: "OPENAI_API_BASE", value: V1, snippet: `aider --model openai/${CODE_MODEL}` },
  { name: "Continue.dev", variable: "apiBase", value: V1, snippet: `provider: openai · model: ${CODE_MODEL}` },
  { name: "OpenClaw", variable: "base_url", value: V1, snippet: "provider: openai-compatible" },
  { name: "LangChain", variable: "base_url", value: V1, snippet: `ChatOpenAI(model="${CODE_MODEL}", base_url=...)` },
];

export const ENDPOINTS: { method: string; path: string; desc: string; code: string }[] = [
  {
    method: "POST",
    path: "/v1/chat/completions",
    desc: "OpenAI Chat Completions. JSON or SSE streaming; usage is always metered (stream_options.include_usage is added automatically).",
    code: `curl ${V1}/chat/completions \\
  -H "Authorization: Bearer ${KEY}" -H "Content-Type: application/json" \\
  -d '{"model":"${CHAT_MODEL}","messages":[{"role":"user","content":"Hi"}],"max_tokens":2048}'`,
  },
  {
    method: "POST",
    path: "/v1/responses",
    desc: "OpenAI Responses API for Codex-style agents. Accepts input + instructions, streams response.* events.",
    code: `curl ${V1}/responses \\
  -H "Authorization: Bearer ${KEY}" -H "Content-Type: application/json" \\
  -d '{"model":"${CODE_MODEL}","input":"Hello"}'`,
  },
  {
    method: "POST",
    path: "/v1/messages",
    desc: "Anthropic Messages. Requires anthropic-version and a positive max_tokens; supports system, multi-block content and tools.",
    code: `curl ${V1}/messages \\
  -H "x-api-key: ${KEY}" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \\
  -d '{"model":"${CHAT_MODEL}","max_tokens":1024,"messages":[{"role":"user","content":"Hi"}]}'`,
  },
  {
    method: "GET",
    path: "/v1/models",
    desc: "Lists the model IDs enabled for your key. Model IDs are case-sensitive.",
    code: `curl ${V1}/models -H "Authorization: Bearer ${KEY}"`,
  },
  {
    method: "POST",
    path: "/v1/images/generations",
    desc: "Image generation. Returns gateway-hosted URLs or base64 images.",
    code: `curl ${V1}/images/generations \\
  -H "Authorization: Bearer ${KEY}" -H "Content-Type: application/json" \\
  -d '{"model":"kira-3.0-image","prompt":"A lighthouse at dusk, film photo"}'`,
  },
  {
    method: "POST",
    path: "/v1/audio/speech",
    desc: "Text-to-speech. Returns binary audio.",
    code: `curl ${V1}/audio/speech -o speech.mp3 \\
  -H "Authorization: Bearer ${KEY}" -H "Content-Type: application/json" \\
  -d '{"model":"kira-3.0-flash-tts","input":"Welcome to Rev9.","voice":"alloy"}'`,
  },
  {
    method: "POST",
    path: "/v1/videos/generations",
    desc: "Async video generation. Returns an operation id and poll_url; poll GET /v1/videos/{id} until status is completed.",
    code: `curl ${V1}/videos/generations \\
  -H "Authorization: Bearer ${KEY}" -H "Content-Type: application/json" \\
  -d '{"model":"kira-3.0-video","prompt":"Drone shot over a coastline"}'

curl ${V1}/videos/OPERATION_ID -H "Authorization: Bearer ${KEY}"`,
  },
];

export const ERRORS: { status: number; code: string; cause: string; fix: string }[] = [
  { status: 400, code: "http_400 / invalid_request", cause: "Body is not a JSON object, model or messages missing, stream not boolean, or the model rejected the input.", fix: "Validate JSON; send a non-empty messages array (max 256) and a model string." },
  { status: 401, code: "http_401", cause: "Missing or unknown key.", fix: "Send Authorization: Bearer mgw_… or x-api-key: mgw_…" },
  { status: 403, code: "http_403", cause: "The model is not enabled for your key.", fix: "Call GET /v1/models and use an exact (case-sensitive) ID from the list." },
  { status: 404, code: "http_404", cause: "Unknown route or expired video operation.", fix: "Use the routes in the API reference; video results expire after retention." },
  { status: 413, code: "http_413", cause: "Request body larger than the gateway limit (2 MB by default).", fix: "Trim context or send images by URL." },
  { status: 429, code: "http_429 / rate_limited", cause: "Per-key rate limit (120 req/min by default), repeated auth failures, or upstream rate limiting.", fix: "Back off with jitter and retry; see the snippet below." },
  { status: 502, code: "upstream_error / upstream_unavailable", cause: "The model backend errored or is temporarily unavailable.", fix: "Retry after a short delay, or switch to another model." },
  { status: 503, code: "http_503", cause: "The gateway concurrency limit was reached.", fix: "Retry shortly; spread bursts over time." },
  { status: 504, code: "upstream_timeout", cause: "The model did not respond within the timeout.", fix: "Use stream: true for long generations, or lower max_tokens." },
];

export const ERROR_ENVELOPE = `{
  "error": {
    "message": "This model is not enabled for the endpoint",
    "type": "gateway_error",
    "code": "http_403"
  }
}`;

export const RETRY_CODE: CodeTab[] = [
  {
    label: "Python",
    code: `import random, time
from openai import OpenAI, APIConnectionError, RateLimitError, InternalServerError

client = OpenAI(base_url="${V1}", api_key="${KEY}")

def query_with_retry(prompt: str, max_retries: int = 4):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model="${CHAT_MODEL}",
                messages=[{"role": "user", "content": prompt}],
            )
        except (RateLimitError, APIConnectionError, InternalServerError):
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt + random.uniform(0.1, 0.5))`,
  },
  {
    label: "TypeScript",
    code: `async function withRetry<T>(fn: () => Promise<T>, retries = 4): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn();
    } catch (error: any) {
      const status = error?.status ?? 0;
      if (attempt >= retries - 1 || ![429, 502, 503, 504].includes(status)) throw error;
      await new Promise((r) => setTimeout(r, 2 ** attempt * 1000 + Math.random() * 400));
    }
  }
}`,
  },
];
