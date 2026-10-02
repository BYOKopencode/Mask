"""Self-hosted OpenAI-compatible model gateway with a protected web dashboard.

Install: pip install fastapi "uvicorn[standard]" httpx cryptography aiosqlite
Required: DASHBOARD_PASSWORD and FERNET_KEY (run Fernet.generate_key() once).
Run:      uvicorn mask:app --host 0.0.0.0 --port 8000

Production configuration (set in the process environment):
  COOKIE_SECURE=true                 # Required when the dashboard uses HTTPS.
  SESSION_SECRET=<unique-random-secret>  # Separate from FERNET_KEY.
  REQUEST_TIMEOUT=300                # Max upstream connect/read/write seconds.
  MAX_REQUEST_BYTES=2000000          # Limit accepted JSON request size.
  MAX_CONCURRENT_REQUESTS=100         # Protect the gateway and upstreams.
  UPSTREAM_ALLOWED_HOSTS=api.example.com,api.openai.com  # Optional allowlist.
  ALLOW_INSECURE_UPSTREAMS=true     # Permit http:// upstream APIs; HTTPS remains the default.
  DOMAIN=api.yuvraj.pro            # Public hostname used in generated API/media URLs.
  TRUST_PROXY_HEADERS=true          # Use CF-Connecting-IP / X-Forwarded-For (behind Cloudflare).
  PUBLIC_RATE_LIMIT_PER_MINUTE=120  # Per-IP limit for public telemetry endpoints.
  LOGIN_MAX_ATTEMPTS=8              # Failed /rev9 logins allowed per IP per 15 minutes.
  PUBLIC_CORS_ORIGINS=*             # Origins allowed to read public telemetry (GET only).
"""

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import aiosqlite
import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

def _load_env_file(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file into the environment (no overrides)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass

_load_env_file()


def positive_int_env(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer setting and fail closed on invalid configuration."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def positive_float_env(name: str, default: float, minimum: float = 0.1) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


DB_PATH = os.getenv("GATEWAY_DB", "gateway.db")
TIMEOUT = positive_float_env("REQUEST_TIMEOUT", 300)
CONNECT_TIMEOUT = positive_float_env("CONNECT_TIMEOUT", 15)
QUEUE_TIMEOUT = positive_float_env("QUEUE_TIMEOUT", 15)
MAX_REQUEST_BYTES = positive_int_env("MAX_REQUEST_BYTES", 2_000_000)
MAX_MESSAGES = positive_int_env("MAX_MESSAGES", 256)
MAX_CONCURRENT_REQUESTS = positive_int_env("MAX_CONCURRENT_REQUESTS", 100)
RATE_LIMIT_PER_MINUTE = positive_int_env("RATE_LIMIT_PER_MINUTE", 120)
MAX_AUTH_FAILURES_PER_MINUTE = positive_int_env("MAX_AUTH_FAILURES_PER_MINUTE", 30)
LOG_RETENTION_DAYS = positive_int_env("LOG_RETENTION_DAYS", 30)
VIDEO_POLL_INTERVAL = positive_float_env("VIDEO_POLL_INTERVAL", 5)
VIDEO_POLL_MAX_ATTEMPTS = positive_int_env("VIDEO_POLL_MAX_ATTEMPTS", 60)
VIDEO_POLL_TIMEOUT = positive_float_env("VIDEO_POLL_TIMEOUT", 300)
UPSTREAM_MAX_CONNECTIONS = positive_int_env("UPSTREAM_MAX_CONNECTIONS", 100)
UPSTREAM_KEEPALIVE_CONNECTIONS = positive_int_env("UPSTREAM_KEEPALIVE_CONNECTIONS", 20)
ALLOW_INSECURE_UPSTREAMS = os.getenv("ALLOW_INSECURE_UPSTREAMS", "false").lower() == "true"
UPSTREAM_ALLOWED_HOSTS = {host.strip().lower() for host in os.getenv("UPSTREAM_ALLOWED_HOSTS", "").split(",") if host.strip()}
ADMIN_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
FERNET_KEY = os.getenv("FERNET_KEY", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "api.yuvraj.pro").strip().rstrip("/")
PUBLIC_ORIGIN = (f"https://{DOMAIN}" if DOMAIN else "").rstrip("/")
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "true").lower() == "true"
PUBLIC_RATE_LIMIT_PER_MINUTE = positive_int_env("PUBLIC_RATE_LIMIT_PER_MINUTE", 120)
LOGIN_MAX_ATTEMPTS = positive_int_env("LOGIN_MAX_ATTEMPTS", 8)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true" if DOMAIN else "false").lower() == "true"
SESSION_MAX_AGE = positive_int_env("SESSION_MAX_AGE", 8 * 3600, 300)
PUBLIC_CORS_ORIGINS = {o.strip() for o in os.getenv("PUBLIC_CORS_ORIGINS", "*").split(",") if o.strip()}
PUBLIC_TELEMETRY_PATHS = ("/models/public", "/requests/public", "/stats/public", "/health/providers/public")

# Keep strong references to fire-and-forget tasks so they are not garbage
# collected before they finish (a real footgun on Python 3.12+).
_background_tasks: set[asyncio.Task[Any]] = set()
_rate_buckets: dict[str, list[float]] = {}
_auth_failures: dict[str, list[float]] = {}


def spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def fail_if_unconfigured() -> None:
    if not ADMIN_PASSWORD or not FERNET_KEY or not SESSION_SECRET:
        raise RuntimeError("Set DASHBOARD_PASSWORD, FERNET_KEY, and a separate SESSION_SECRET before starting the gateway.")
    if UPSTREAM_KEEPALIVE_CONNECTIONS > UPSTREAM_MAX_CONNECTIONS:
        raise RuntimeError("UPSTREAM_KEEPALIVE_CONNECTIONS cannot exceed UPSTREAM_MAX_CONNECTIONS")
    try:
        Fernet(FERNET_KEY.encode())
    except (ValueError, TypeError) as error:
        raise RuntimeError("FERNET_KEY must be a valid Fernet key.") from error


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS providers (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      public_id TEXT NOT NULL UNIQUE, chat_url TEXT NOT NULL, models_url TEXT,
      encrypted_key TEXT NOT NULL, auth_header TEXT NOT NULL DEFAULT 'Authorization',
      auth_prefix TEXT NOT NULL DEFAULT 'Bearer', allowed_models TEXT NOT NULL DEFAULT '[]',
      client_key_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS request_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id INTEGER NOT NULL, model TEXT,
      api_name TEXT NOT NULL DEFAULT 'chat', status INTEGER NOT NULL, prompt_tokens INTEGER NOT NULL DEFAULT 0,
      completion_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0,
      latency_ms INTEGER NOT NULL, error TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_logs_id ON request_logs(id DESC);
    CREATE INDEX IF NOT EXISTS idx_logs_provider ON request_logs(provider_id, id DESC);
    CREATE TABLE IF NOT EXISTS provider_health (
      provider_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'unknown',
      latency_ms INTEGER NOT NULL DEFAULT 0, checked_at TEXT, error TEXT
    );
    CREATE TABLE IF NOT EXISTS media_assets (
      id TEXT PRIMARY KEY, provider_id INTEGER NOT NULL, source_url TEXT NOT NULL,
      media_type TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS media_operations (
      id TEXT PRIMARY KEY, provider_id INTEGER NOT NULL, operation_name TEXT NOT NULL,
      media_type TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_media_created ON media_assets(created_at);
    CREATE INDEX IF NOT EXISTS idx_operations_created ON media_operations(created_at);
    CREATE TABLE IF NOT EXISTS video_outputs (
      operation_id TEXT PRIMARY KEY, body BLOB NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS media_blobs (
      id TEXT PRIMARY KEY, body BLOB NOT NULL, media_type TEXT NOT NULL, created_at TEXT NOT NULL
    );
    """)
    await conn.commit()
    try:
        await conn.execute("ALTER TABLE request_logs ADD COLUMN api_name TEXT NOT NULL DEFAULT 'chat'")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    try:
        await conn.execute("ALTER TABLE request_logs ADD COLUMN provider_name TEXT NOT NULL DEFAULT ''")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    for column in ("ttft_ms INTEGER", "streamed INTEGER NOT NULL DEFAULT 0"):
        try:
            await conn.execute(f"ALTER TABLE request_logs ADD COLUMN {column}")
            await conn.commit()
        except aiosqlite.OperationalError:
            pass
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_api ON request_logs(api_name, id DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_created ON request_logs(created_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_model ON request_logs(model)")
    await conn.execute("""CREATE TABLE IF NOT EXISTS model_specs (
      model TEXT PRIMARY KEY, context_window INTEGER, max_output_tokens INTEGER, updated_at TEXT NOT NULL)""")
    await conn.execute("CREATE TABLE IF NOT EXISTS gateway_counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    # Lifetime counters survive log retention; seed them once from existing logs.
    await conn.execute("INSERT OR IGNORE INTO gateway_counters SELECT 'requests', COUNT(*) FROM request_logs")
    await conn.execute("INSERT OR IGNORE INTO gateway_counters SELECT 'tokens', COALESCE(SUM(total_tokens),0) FROM request_logs")
    await conn.execute("DELETE FROM request_logs WHERE created_at < datetime('now', ?)", (f'-{LOG_RETENTION_DAYS} days',))
    await conn.execute("DELETE FROM media_assets WHERE created_at < datetime('now', ?)", (f'-{LOG_RETENTION_DAYS} days',))
    await conn.execute("DELETE FROM media_operations WHERE created_at < datetime('now', ?)", (f'-{LOG_RETENTION_DAYS} days',))
    await conn.execute("DELETE FROM video_outputs WHERE operation_id NOT IN (SELECT id FROM media_operations)")
    await conn.execute("DELETE FROM media_blobs WHERE created_at < datetime('now', ?)", (f'-{LOG_RETENTION_DAYS} days',))
    await conn.commit()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def crypt() -> Fernet:
    return Fernet(FERNET_KEY.encode())


def encrypt(value: str) -> str:
    return crypt().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return crypt().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise HTTPException(status_code=500, detail="Stored upstream key cannot be decrypted") from error


def hash_key(value: str) -> str:
    # maxmem must be raised for scrypt with n=2**14 on some builds.
    return hashlib.scrypt(value.encode(), salt=b"model-gateway-v1", n=2**14, r=8, p=1, maxmem=2**25).hex()


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    schemes = {"https", "http"} if ALLOW_INSECURE_UPSTREAMS else {"https"}
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password:
        return False
    return not UPSTREAM_ALLOWED_HOSTS or parsed.hostname.lower() in UPSTREAM_ALLOWED_HOSTS


async def request_json_object(request: Request) -> dict[str, Any]:
    """Decode a bounded JSON object; never let clients exhaust memory with a body."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too large")
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from error
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="Request body is too large")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from error
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    return data


def validate_chat_payload(payload: dict[str, Any]) -> tuple[str, bool]:
    """Apply gateway limits without restricting valid OpenAI content formats."""
    model = payload.get("model")
    messages = payload.get("messages")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise HTTPException(status_code=400, detail="model must be a non-empty string up to 200 characters")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise HTTPException(status_code=400, detail=f"messages must contain 1 to {MAX_MESSAGES} items")
    if not all(isinstance(message, dict) and isinstance(message.get("role"), str) for message in messages):
        raise HTTPException(status_code=400, detail="Each message must be an object with a role")
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise HTTPException(status_code=400, detail="stream must be a boolean")
    return model.strip(), stream


def resolve_provider_urls(chat_url: str, models_url: str = "") -> tuple[str, str]:
    """Expand a standard OpenAI-compatible API base URL into its endpoints."""
    chat_url = chat_url.rstrip("/")
    models_url = models_url.rstrip("/")
    if chat_url.endswith("/v1"):
        api_base = chat_url
        chat_url = f"{api_base}/chat/completions"
        if not models_url:
            models_url = f"{api_base}/models"
    elif chat_url.endswith("/chat/completions") and not models_url:
        models_url = chat_url.removesuffix("/chat/completions") + "/models"
    return chat_url, models_url


def client_key(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-api-key", "")


def client_ip(request: Request) -> str:
    """Best-effort client address; trusts Cloudflare/proxy headers only when configured."""
    if TRUST_PROXY_HEADERS:
        for header in ("cf-connecting-ip", "x-real-ip"):
            value = request.headers.get(header, "").strip()
            if value and len(value) <= 64:
                return value
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64] or "unknown"
    return request.client.host if request.client else "unknown"


def request_id(request: Request) -> str:
    return request.headers.get("x-request-id", "")[:100] or uuid.uuid4().hex


def public_url(request: Request, path: str) -> str:
    origin = PUBLIC_ORIGIN or str(request.base_url).rstrip("/")
    return f"{origin}{path}"


def neutral_filename(media_type: str, extension: str) -> str:
    return f"gateway-{media_type}-{uuid.uuid4().hex[:12]}.{extension}"

def sanitize_log_error(message: str) -> str:
    """Keep error text short and free of upstream provider identifiers."""
    text = " ".join(str(message or "").split())
    return text[:300]

def normalize_video_status(raw: Any) -> str:
    """Map varied upstream status spellings onto the gateway's three public states."""
    value = str(raw or "").strip().lower()
    if value in {"completed", "succeeded", "success", "done", "ok", "finished"}:
        return "completed"
    if value in {"failed", "error", "cancelled", "canceled", "expired"}:
        return "failed"
    return "processing"

def decode_b64_media(encoded: Any, max_bytes: int) -> bytes | None:
    """Strictly decode a base64 media payload; None when invalid or oversized."""
    import base64
    import binascii
    if not isinstance(encoded, str) or not encoded or len(encoded) > max_bytes:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    return decoded or None

def extract_video_output(data: Any) -> str:
    """Find the first video URL in documented upstream response shapes.

    Handles flat {url|uri|video_url}, {output: {url|...}}, {output: {video: {url|uri}}},
    {output: [...]}, and Vertex-style {response: {generateVideoResponse:
    {generatedSamples: [{video: {uri}}] | [{uri}]}}} without inventing a poll endpoint.
    """
    seen: set[int] = set()

    def walk(node: Any) -> str:
        if isinstance(node, dict):
            if id(node) in seen:
                return ""
            seen.add(id(node))
            for key in ("url", "uri", "video_url", "videoUri", "gcsUri"):
                value = node.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
            for key in ("output", "result", "response", "video", "generateVideoResponse",
                        "generatedSamples", "samples", "data"):
                if key in node:
                    found = walk(node[key])
                    if found:
                        return found
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(data)

async def create_media_operation(app: FastAPI, provider_id: int, provider_name: str,
                                 operation_name: str, media_type: str, model: str) -> str:
    """Persist a pending media operation; the public id is generated here."""
    gateway_operation = uuid.uuid4().hex
    async with app.state.db_lock:
        await app.state.db.execute(
            "INSERT INTO media_operations VALUES (?,?,?,?,?,?)",
            (gateway_operation, provider_id, operation_name, media_type, model, now()))
        await app.state.db.commit()
    return gateway_operation

def upstream_poll_target(provider: aiosqlite.Row, operation_name: str) -> str:
    """Resolve the upstream operation identifier into a pollable URL.

    Only same-origin URLs may receive provider credentials. Vertex resource
    names require a provider-specific contract, not a guessed GET URL.
    """
    name = str(operation_name)
    from urllib.parse import urlsplit
    base = str(provider["chat_url"]).rstrip("/") if "chat_url" in provider.keys() else ""
    if name.startswith(("http://", "https://")):
        target, origin = urlsplit(name), urlsplit(base)
        if not base or (target.scheme, target.hostname, target.port) != (origin.scheme, origin.hostname, origin.port) or target.username or target.password:
            raise ValueError("Untrusted operation URL")
        return name
    if name.startswith("projects/") or "/projects/" in name:
        raise ValueError("Provider-specific video polling is not configured")
    if not base:
        return name
    if base.endswith("/chat/completions"):
        base = base.removesuffix("/chat/completions")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/{name.lstrip('/')}"

async def poll_video_operation(app: FastAPI, request: Request, public_id: str,
                               operation_id: str, provider: aiosqlite.Row,
                               operation: aiosqlite.Row,
                               max_attempts: int | None = None) -> dict[str, Any]:
    """Poll the upstream operation until it completes, fails, or times out.

    The stored operation_name is the upstream's own operation identifier, used only
    for authenticated upstream polling. It never appears in the public response.
    """
    conn = app.state.db
    headers = {provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}".strip(),
               "Accept": "application/json"}
    attempts = max_attempts or VIDEO_POLL_MAX_ATTEMPTS
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    last_error = ""
    for _ in range(attempts):
        if time.monotonic() > deadline:
            last_error = "video generation polling timed out"
            break
        try:
            async with app.state.upstream_slots:
                upstream = await app.state.http.get(upstream_poll_target(provider, operation["operation_name"]), headers=headers)
            try:
                upstream.raise_for_status()
                data = upstream.json()
            finally:
                await upstream.aclose()
        except (httpx.HTTPError, ValueError) as error:
            last_error = "video operation unavailable"
            await asyncio.sleep(min(VIDEO_POLL_INTERVAL, 1.0))
            continue
        status = normalize_video_status(data.get("status") if isinstance(data, dict) else None)
        done = bool(data.get("done")) if isinstance(data, dict) else False
        output_url = extract_video_output(data)
        if status != "completed" and done:
            # Vertex-style bodies carry no status field; done without output means failure.
            status = "completed" if output_url else "failed"
        if status == "failed":
            message = "video generation failed"
            return {"id": operation_id, "object": "video_operation", "status": "failed",
                    "model": operation["model"], "output": None, "error": message}
        if status == "completed" and output_url:
            asset_id = uuid.uuid4().hex
            async with app.state.db_lock:
                await conn.execute("INSERT INTO media_assets VALUES (?,?,?,?,?)",
                                   (asset_id, provider["id"], output_url, operation["media_type"], now()))
                await conn.commit()
            return {"id": operation_id, "object": "video_operation", "status": "completed",
                    "model": operation["model"],
                    "output": public_url(request, f"/media/{asset_id}")}
        await asyncio.sleep(min(VIDEO_POLL_INTERVAL, 1.0))
    return {"id": operation_id, "object": "video_operation", "status": "failed",
            "model": operation["model"], "output": None,
            "error": last_error or "video generation polling timed out"}

def video_status_error(code: str, upstream_status: int | None = None) -> HTTPException:
    # Never include upstream bodies, URLs, headers, or exception text.
    logging.getLogger("uvicorn.error").warning(
        "video_status_check code=%s upstream_status=%s", code, upstream_status)
    detail = {"code": code, "message": "Video status check failed; generation status is unknown"}
    if upstream_status is not None:
        detail["upstream_status"] = upstream_status
    return HTTPException(status_code=502, detail=detail)


async def public_operation_payload(app: FastAPI, request: Request, public_id: str,
                                   operation_id: str, provider: aiosqlite.Row,
                                   operation: aiosqlite.Row) -> dict[str, Any]:
    """Resolve an operation once and return the public, provider-neutral body."""
    conn = app.state.db
    async with conn.execute("SELECT operation_id FROM video_outputs WHERE operation_id=?", (operation_id,)) as cur:
        if await cur.fetchone():
            return {"id": operation_id, "object": "video_operation", "status": "completed",
                    "model": operation["model"], "output": public_url(request, f"/media/{operation_id}")}
    headers = {provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}".strip(),
               "Accept": "application/json"}
    try:
        target = upstream_poll_target(provider, operation["operation_name"])
    except ValueError:
        raise video_status_error("video_poll_target_invalid") from None
    try:
        async with app.state.upstream_slots:
            upstream = await app.state.http.get(target, headers=headers)
        try:
            if upstream.status_code >= 300:
                raise video_status_error("video_poll_http_error", upstream.status_code)
            upstream.raise_for_status()
            data = upstream.json()
        finally:
            await upstream.aclose()
    except ValueError:
        raise video_status_error("video_poll_invalid_json") from None
    except httpx.TimeoutException:
        raise video_status_error("video_poll_timeout") from None
    except httpx.HTTPError:
        raise video_status_error("video_poll_transport_error") from None
    if not isinstance(data, dict) or not any(key in data for key in ("done", "status", "error")):
        raise video_status_error("video_poll_invalid_payload")
    if isinstance(data, dict) and data.get("error"):
        return {"id": operation_id, "object": "video_operation", "status": "failed",
                "model": operation["model"], "output": None, "error": "video generation failed"}
    if isinstance(data, dict) and (data.get("done") or normalize_video_status(data.get("status")) == "completed"):
        items = data.get("data")
        encoded = items[0].get("b64_json") if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if encoded is not None:
            video = decode_b64_media(encoded, 140_000_000)
            if video is None:
                return {"id": operation_id, "object": "video_operation", "status": "failed",
                        "model": operation["model"], "output": None, "error": "Invalid video output"}
            async with app.state.db_lock:
                await conn.execute("INSERT OR IGNORE INTO video_outputs VALUES (?,?,?)", (operation_id, video, now()))
                await conn.commit()
            return {"id": operation_id, "object": "video_operation", "status": "completed",
                    "model": operation["model"], "output": public_url(request, f"/media/{operation_id}")}
    status = normalize_video_status(data.get("status") if isinstance(data, dict) else None)
    done = bool(data.get("done")) if isinstance(data, dict) else False
    output_url = extract_video_output(data)
    if status != "completed" and done:
        status = "completed" if output_url else "failed"
    if status == "completed" and output_url:
        asset_id = uuid.uuid4().hex
        async with app.state.db_lock:
            await conn.execute("INSERT INTO media_assets VALUES (?,?,?,?,?)",
                               (asset_id, provider["id"], output_url, operation["media_type"], now()))
            await conn.commit()
        return {"id": operation_id, "object": "video_operation", "status": "completed",
                "model": operation["model"], "output": public_url(request, f"/media/{asset_id}")}
    if status == "failed":
        message = "video generation failed"
        return {"id": operation_id, "object": "video_operation", "status": "failed",
                "model": operation["model"], "output": None, "error": message}
    return {"id": operation_id, "object": "video_operation", "status": "processing",
            "model": operation["model"], "output": None}


async def media_provider(public_id: str, request: Request) -> aiosqlite.Row:
    return await public_provider(public_id, request)


async def media_request(public_id: str, request: Request, kind: str, endpoint: str,
                        response_type: str = "json") -> Response:
    provider = await media_provider(public_id, request)
    payload = await request_json_object(request)
    model = str(payload.get("model", kind)).strip()[:200]
    if model not in json.loads(provider["allowed_models"]):
        raise HTTPException(status_code=403, detail="This model is not enabled for the endpoint")
    base = provider["chat_url"].rstrip("/")
    if base.endswith("/chat/completions"):
        base = base.removesuffix("/chat/completions")
    if base.endswith("/v1"):
        upstream_url = f"{base}/{endpoint.lstrip('/')}"
    else:
        upstream_url = f"{base}/v1/{endpoint.lstrip('/')}"
    headers = {"Accept": "application/json", provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}".strip()}
    started = time.monotonic()
    try:
        async with request.app.state.upstream_slots:
            upstream = await request.app.state.http.post(upstream_url, headers=headers, json=payload)
    except httpx.TimeoutException as error:
        await record(request.app, provider["id"], model, 504, started, api_name=kind, error="media upstream timed out")
        raise HTTPException(status_code=504, detail="Media provider timed out") from error
    except httpx.RequestError as error:
        await record(request.app, provider["id"], model, 502, started, api_name=kind, error="media upstream unavailable")
        raise HTTPException(status_code=502, detail="Media provider unavailable") from error
    if upstream.status_code >= 400:
        await record(request.app, provider["id"], model, upstream.status_code, started, api_name=kind, error=f"media upstream HTTP {upstream.status_code}")
        await upstream.aclose()
        return masked_upstream_error(upstream.status_code)
    body = await upstream.aread()
    await upstream.aclose()
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        await record(request.app, provider["id"], model, upstream.status_code, started, api_name=kind)
        return Response(body, status_code=upstream.status_code, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{neutral_filename(kind, "bin")}"'})
    if kind == "image":
        import base64
        import binascii
        results = []
        try:
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("Missing image output")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid image output")
                asset_id = uuid.uuid4().hex
                if "b64_json" in item:
                    encoded = item["b64_json"]
                    if not isinstance(encoded, str) or len(encoded) > 40_000_000:
                        raise ValueError("Invalid image size")
                    image = base64.b64decode(encoded, validate=True)
                    mime = "image/png" if image.startswith(b'\x89PNG\r\n\x1a\n') else "image/jpeg" if image.startswith(b'\xff\xd8\xff') else "image/webp" if image[:4] == b'RIFF' and image[8:12] == b'WEBP' else ""
                    if not mime:
                        raise ValueError("Unsupported image format")
                    async with request.app.state.db_lock:
                        await request.app.state.db.execute("INSERT INTO media_blobs VALUES (?,?,?,?)", (asset_id, image, mime, now()))
                        await request.app.state.db.commit()
                else:
                    from urllib.parse import urlsplit
                    url = item.get("url", "")
                    if not isinstance(url, str) or urlsplit(url).scheme != "https":
                        raise ValueError("Invalid image URL")
                    async with request.app.state.db_lock:
                        await request.app.state.db.execute("INSERT INTO media_assets VALUES (?,?,?,?,?)", (asset_id, provider["id"], url, "image", now()))
                        await request.app.state.db.commit()
                results.append({"url": public_url(request, f"/media/{asset_id}")})
        except (ValueError, TypeError, binascii.Error):
            await record(request.app, provider["id"], model, 502, started, api_name=kind, error="Invalid image output")
            return masked_upstream_error(502)
        await record(request.app, provider["id"], model, upstream.status_code, started,
                     usage=data.get("usage") if isinstance(data.get("usage"), dict) else None, api_name=kind)
        return JSONResponse({"created": int(time.time()), "data": results}, status_code=upstream.status_code)
    if response_type == "json":
        if kind == "image" and isinstance(data.get("data"), list):
            for item in data["data"]:
                if isinstance(item, dict) and item.get("url"):
                    asset_id = uuid.uuid4().hex
                    async with request.app.state.db_lock:
                        await request.app.state.db.execute("INSERT INTO media_assets VALUES (?,?,?,?,?)", (asset_id, provider["id"], item["url"], "image", now()))
                        await request.app.state.db.commit()
                    item["url"] = public_url(request, f"/media/{asset_id}")
        if kind == "video" and isinstance(data.get("data"), list):
            for item in data["data"]:
                if isinstance(item, dict) and item.get("url"):
                    asset_id = uuid.uuid4().hex
                    async with request.app.state.db_lock:
                        await request.app.state.db.execute("INSERT INTO media_assets VALUES (?,?,?,?,?)", (asset_id, provider["id"], item["url"], "video", now()))
                        await request.app.state.db.commit()
                    item["url"] = public_url(request, f"/media/{asset_id}")
        body = json.dumps(data).encode()
    if kind == "video" and isinstance(data, dict):
        # Synchronous completed video responses carry data[].b64_json; decode and
        # serve them through the gateway instead of leaking raw base64 upstream JSON.
        items = data.get("data")
        encoded = items[0].get("b64_json") if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if encoded is not None and (data.get("done") or normalize_video_status(data.get("status")) == "completed"):
            video = decode_b64_media(encoded, 140_000_000)
            if video is None:
                await record(request.app, provider["id"], model, 502, started, api_name=kind, error="Invalid video output")
                return masked_upstream_error(502)
            gateway_operation = await create_media_operation(request.app, provider_id=provider["id"],
                                         provider_name=provider["name"],
                                         operation_name="sync-completed",
                                         media_type=kind, model=model)
            async with request.app.state.db_lock:
                await request.app.state.db.execute("INSERT OR IGNORE INTO video_outputs VALUES (?,?,?)",
                                                   (gateway_operation, video, now()))
                await request.app.state.db.commit()
            data = {"id": gateway_operation, "object": "video_operation", "status": "completed",
                    "model": model, "output": public_url(request, f"/media/{gateway_operation}")}
            body = json.dumps(data).encode()
    if kind == "video" and isinstance(data, dict) and (data.get("operation_name") or (data.get("id") and normalize_video_status(data.get("status")) == "processing")):
        from urllib.parse import quote
        upstream_id = str(data.get("id") or "")
        if not upstream_id:
            return masked_upstream_error(502)
        api_base = base if base.endswith("/v1") else f"{base}/v1"
        gateway_operation = await create_media_operation(request.app, provider_id=provider["id"],
                                     provider_name=provider["name"],
                                     operation_name=f"{api_base}/videos/operations/{quote(upstream_id, safe='')}",
                                     media_type=kind, model=model)
        data = {"id": gateway_operation, "object": "video_operation",
                "status": normalize_video_status(data.get("status")), "model": model, "output": None,
                "poll_url": public_url(request, f"/v1/videos/{gateway_operation}")}
        body = json.dumps(data).encode()
    await record(request.app, provider["id"], model, upstream.status_code, started, api_name=kind)
    return Response(body, status_code=upstream.status_code, media_type="application/json")


def allow_rate(key: str, limit: int, window: float = 60.0) -> bool:
    current = time.monotonic()
    if len(_rate_buckets) > 50_000:
        # Bound memory: drop buckets with no activity in the last 15 minutes.
        for stale in [k for k, v in _rate_buckets.items() if not v or current - v[-1] > 900]:
            _rate_buckets.pop(stale, None)
    values = [stamp for stamp in _rate_buckets.get(key, []) if current - stamp < window]
    if len(values) >= limit:
        _rate_buckets[key] = values
        return False
    values.append(current)
    _rate_buckets[key] = values
    return True


async def public_provider(public_id: str, request: Request) -> aiosqlite.Row:
    cached = getattr(request.state, "authenticated_provider", None)
    if cached is not None and cached["public_id"] == public_id:
        return cached
    conn = request.app.state.db
    async with conn.execute("SELECT * FROM providers WHERE public_id = ?", (public_id,)) as cur:
        row = await cur.fetchone()
    if not row or not row["enabled"]:
        raise HTTPException(status_code=404, detail="Gateway endpoint not found")
    presented = client_key(request)
    # Reject empty keys before doing an (irrelevant) constant-time compare.
    if not presented or len(presented) > 512 or not hmac.compare_digest(hash_key(presented), row["client_key_hash"]):
        if not allow_rate(f"auth:{client_ip(request)}", MAX_AUTH_FAILURES_PER_MINUTE):
            raise HTTPException(status_code=429, detail="Too many authentication failures")
        raise HTTPException(status_code=401, detail="Invalid gateway API key")
    if not allow_rate(f"key:{row['id']}:{hash_key(presented)[:16]}", RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Gateway rate limit exceeded")
    return row

async def provider_for_clean_v1(request: Request) -> aiosqlite.Row:
    """Resolve a provider from the bearer key for the identifier-free /v1 API."""
    presented = client_key(request)
    if not presented or len(presented) > 512:
        raise HTTPException(status_code=401, detail="Invalid gateway API key")
    presented_hash = hash_key(presented)
    conn = request.app.state.db
    async with conn.execute(
        "SELECT * FROM providers WHERE enabled=1 AND client_key_hash=?",
        (presented_hash,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        if not allow_rate(f"auth:{client_ip(request)}", MAX_AUTH_FAILURES_PER_MINUTE):
            raise HTTPException(status_code=429, detail="Too many authentication failures")
        raise HTTPException(status_code=401, detail="Invalid gateway API key")
    if not allow_rate(f"key:{row['id']}:{presented_hash[:16]}", RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status_code=429, detail="Gateway rate limit exceeded")
    request.state.authenticated_provider = row
    return row


def dashboard_required(request: Request) -> None:
    if not request.session.get("dashboard"):
        raise HTTPException(status_code=401, detail="Dashboard login required")


def csrf_required(request: Request) -> None:
    token = request.headers.get("x-csrf-token", "")
    expected = request.session.get("csrf")
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def safe_provider(row: aiosqlite.Row) -> dict[str, Any]:
    chat_url, models_url = resolve_provider_urls(row["chat_url"], row["models_url"])
    return {
        "id": row["id"], "name": row["name"], "public_id": row["public_id"],
        "chat_url": chat_url, "models_url": models_url,
        "auth_header": row["auth_header"], "auth_prefix": row["auth_prefix"],
        "allowed_models": json.loads(row["allowed_models"]), "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    fail_if_unconfigured()
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    app.state.db = conn
    app.state.db_lock = asyncio.Lock()  # serialize writes on the single connection
    app.state.upstream_slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT),
        limits=httpx.Limits(
            max_connections=UPSTREAM_MAX_CONNECTIONS,
            max_keepalive_connections=UPSTREAM_KEEPALIVE_CONNECTIONS,
        ),
        # Do not unexpectedly send protected upstream requests through a proxy
        # inherited from the host environment.
        trust_env=False,
    )
    try:
        yield
    finally:
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)
        await app.state.http.aclose()
        await conn.close()



# --- Frontend binding (landing page served by the gateway) ---
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = os.getenv("FRONTEND_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "public"))

def _frontend_index() -> HTMLResponse:
    path = os.path.join(FRONTEND_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as handle:
        return HTMLResponse(handle.read())

app = FastAPI(title="Rev9 Apis", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets"), check_dir=False), name="assets")
app.mount("/logos", StaticFiles(directory=os.path.join(FRONTEND_DIR, "logos"), check_dir=False), name="logos")

STATIC_FILES = {"/favicon.png": "image/png", "/icon.png": "image/png", "/logo.png": "image/png",
                "/robots.txt": "text/plain"}


def _static_route(route: str, media_type: str) -> None:
    async def serve() -> Response:
        path = os.path.join(FRONTEND_DIR, route.lstrip("/"))
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
    app.add_api_route(route, serve, methods=["GET"], include_in_schema=False)


for _route, _media in STATIC_FILES.items():
    _static_route(_route, _media)


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="__rev9_session",
    max_age=SESSION_MAX_AGE,
    https_only=COOKIE_SECURE,
    same_site="strict",
)


def _cors_origin(request: Request) -> str:
    origin = request.headers.get("origin", "")
    if not origin:
        return ""
    if "*" in PUBLIC_CORS_ORIGINS:
        return "*"
    return origin if origin in PUBLIC_CORS_ORIGINS else ""


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    """Browser protections, public-telemetry CORS and per-IP throttling."""
    path = request.url.path
    is_public_telemetry = path in PUBLIC_TELEMETRY_PATHS
    if is_public_telemetry:
        if request.method == "OPTIONS":
            response: Response = Response(status_code=204)
        elif request.method != "GET":
            response = JSONResponse({"detail": "Method not allowed"}, status_code=405)
        elif not allow_rate(f"public:{client_ip(request)}", PUBLIC_RATE_LIMIT_PER_MINUTE):
            response = JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "30"})
        else:
            response = await call_next(request)
        origin = _cors_origin(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Max-Age"] = "600"
            response.headers["Vary"] = "Origin"
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()")
    response.headers.setdefault("X-Request-ID", request_id(request))
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    if "server" in response.headers:
        del response.headers["server"]
    if path.startswith(("/assets/", "/logos/")):
        response.headers.setdefault("Cache-Control", "public, max-age=604800, immutable")
    if path.startswith(("/rev9", "/api/", "/logout", "/health/providers")) and not is_public_telemetry:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    if path in {"/", "/rev9"} or "." not in path.rsplit("/", 1)[-1]:
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "script-src 'self' 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
    return response


@app.exception_handler(HTTPException)
async def http_exception(request: Request, error: HTTPException) -> JSONResponse:
    """Return the error envelope expected by OpenAI SDKs on public endpoints."""
    if request.url.path.startswith(("/p/", "/v1/")):
        detail = error.detail if isinstance(error.detail, str) else "Gateway request failed"
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"message": detail, "type": "gateway_error", "code": f"http_{error.status_code}"}},
            headers=error.headers,
        )
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail}, headers=error.headers)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "model-gateway"}


@app.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    """Readiness probe for load balancers: confirms the persistent store is usable."""
    try:
        async with request.app.state.db.execute("SELECT 1") as cur:
            await cur.fetchone()
    except aiosqlite.Error as error:
        raise HTTPException(status_code=503, detail="Gateway database is unavailable") from error
    return {"status": "ready"}


@app.get("/health/providers")
async def provider_health(request: Request) -> dict[str, Any]:
    dashboard_required(request)
    conn = request.app.state.db
    async with conn.execute("""SELECT p.id,p.name,p.enabled,h.status,h.latency_ms,h.checked_at,h.error
                              FROM providers p LEFT JOIN provider_health h ON h.provider_id=p.id
                              ORDER BY p.id DESC""") as cur:
        rows = await cur.fetchall()
    return {"providers": [dict(row) for row in rows]}


@app.get("/health/providers/public")
async def public_provider_health(request: Request) -> dict[str, Any]:
    """Expose aggregate provider health without leaking configuration or errors."""
    conn = request.app.state.db
    async with conn.execute("""SELECT h.status,h.latency_ms,h.checked_at
                              FROM providers p LEFT JOIN provider_health h ON h.provider_id=p.id
                              WHERE p.enabled=1
                              ORDER BY p.id DESC""") as cur:
        rows = await cur.fetchall()
    providers = [dict(row) for row in rows]
    checked = [row for row in providers if row["checked_at"]]
    healthy = sum(row["status"] == "healthy" for row in providers)
    return {
        "providers": {
            "enabled": len(providers),
            "healthy": healthy,
            "checked": len(checked),
        },
        "observations": providers,
    }


# Published context / max-output limits per model family. Longest prefix wins;
# admins can override any model from /rev9 (stored in model_specs).
DEFAULT_MODEL_SPECS: list[tuple[str, int | None, int | None, str]] = [
    ("claude-opus", 200_000, 64_000, "text"),
    ("claude-sonnet", 200_000, 64_000, "text"),
    ("claude-haiku", 200_000, 64_000, "text"),
    ("gpt-6", 400_000, 128_000, "text"),
    ("gpt-5", 400_000, 128_000, "text"),
    ("gpt-4.1", 1_047_576, 32_768, "text"),
    ("gpt-4o", 128_000, 16_384, "text"),
    ("grok-4", 256_000, 64_000, "text"),
    ("gemini-3", 1_048_576, 65_536, "text"),
    ("gemini-2.5", 1_048_576, 65_536, "text"),
    ("deepseek-v4", 1_000_000, 64_000, "text"),
    ("kimi-k3", 1_000_000, 128_000, "text"),
    ("kimi-k2", 256_000, 32_768, "text"),
    ("qwen-3", 256_000, 32_768, "text"),
    ("qwen3", 256_000, 32_768, "text"),
    ("glm-5", 200_000, 128_000, "text"),
    ("minimax-m", 1_000_000, 80_000, "text"),
    ("step-", 256_000, 32_768, "text"),
    ("kira-3.5", 1_048_576, 65_536, "text"),
    ("kira-2.5", 1_048_576, 65_536, "text"),
    ("kira-3.0-flash", 1_048_576, 65_536, "text"),
]
MEDIA_HINTS = (("video", "video"), ("image", "image"), ("tts", "speech"), ("speech", "speech"))


def default_spec(model_id: str) -> tuple[int | None, int | None, str]:
    lowered = model_id.lower()
    kind = next((value for hint, value in MEDIA_HINTS if hint in lowered), "text")
    best: tuple[str, int | None, int | None, str] | None = None
    for entry in DEFAULT_MODEL_SPECS:
        if lowered.startswith(entry[0]) and (best is None or len(entry[0]) > len(best[0])):
            best = entry
    if kind != "text":
        return None, None, kind
    return (best[1], best[2], kind) if best else (None, None, kind)


async def configured_model_ids(conn: aiosqlite.Connection) -> list[str]:
    async with conn.execute("SELECT allowed_models FROM providers WHERE enabled=1") as cur:
        rows = await cur.fetchall()
    seen: dict[str, None] = {}
    for row in rows:
        for model_id in json.loads(row["allowed_models"] or "[]"):
            if isinstance(model_id, str) and model_id:
                seen.setdefault(model_id, None)
    return list(seen)


@app.get("/models/public")
async def public_models(request: Request) -> dict[str, Any]:
    """Enabled models with limits and observed telemetry. Never exposes upstream names."""
    conn = request.app.state.db
    enabled = await configured_model_ids(conn)
    async with conn.execute(
        """SELECT l.model, COUNT(l.id) AS requests,
                  ROUND(AVG(l.latency_ms), 1) AS avg_latency_ms,
                  ROUND(AVG(l.ttft_ms), 1) AS avg_ttft_ms,
                  COALESCE(SUM(l.total_tokens), 0) AS total_tokens,
                  ROUND(AVG(l.completion_tokens), 1) AS avg_output_tokens,
                  MAX(l.completion_tokens) AS max_output_tokens,
                  SUM(CASE WHEN l.status < 400 THEN 1 ELSE 0 END) AS ok,
                  SUM(CASE WHEN l.completion_tokens > 0 AND l.latency_ms > COALESCE(l.ttft_ms, 0)
                      THEN l.completion_tokens ELSE 0 END) AS speed_tokens,
                  SUM(CASE WHEN l.completion_tokens > 0 AND l.latency_ms > COALESCE(l.ttft_ms, 0)
                      THEN (l.latency_ms - COALESCE(l.ttft_ms, 0)) ELSE 0 END) AS speed_ms,
                  MAX(l.created_at) AS last_observed
           FROM request_logs l JOIN providers p ON p.id = l.provider_id AND p.enabled = 1
           WHERE l.model IS NOT NULL AND l.model != ''
           GROUP BY l.model"""
    ) as cur:
        observed_rows = {str(row["model"]): row for row in await cur.fetchall()}
    async with conn.execute("SELECT model, context_window, max_output_tokens FROM model_specs") as cur:
        overrides = {str(row["model"]): row for row in await cur.fetchall()}

    models: list[dict[str, Any]] = []
    for model_id in dict.fromkeys([*enabled, *observed_rows.keys()]):
        context, max_output, kind = default_spec(model_id)
        override = overrides.get(model_id)
        if override is not None:
            context = override["context_window"] or context
            max_output = override["max_output_tokens"] or max_output
        row = observed_rows.get(model_id)
        speed = None
        if row is not None and (row["speed_ms"] or 0) > 0 and (row["speed_tokens"] or 0) > 0:
            speed = round(row["speed_tokens"] / (row["speed_ms"] / 1000), 1)
        requests = int(row["requests"] or 0) if row is not None else 0
        models.append({
            "id": model_id,
            "type": kind,
            "enabled": model_id in enabled,
            "context_window": context,
            "max_output_tokens": max_output,
            "observed": {
                "requests": requests,
                "total_tokens": int(row["total_tokens"] or 0) if row is not None else 0,
                "avg_latency_ms": row["avg_latency_ms"] if row is not None else None,
                "avg_ttft_ms": row["avg_ttft_ms"] if row is not None else None,
                "avg_output_tokens": row["avg_output_tokens"] if row is not None else None,
                "max_output_tokens": row["max_output_tokens"] if row is not None else None,
                "success_rate": round(100 * (row["ok"] or 0) / requests, 1) if requests else None,
                "tokens_per_second": speed,
                "last_observed": row["last_observed"] if row is not None else None,
            },
        })
    models = [m for m in models if m["enabled"]] or models
    models.sort(key=lambda m: (-m["observed"]["requests"], m["id"]))
    return {
        "models": models,
        "semantics": {
            "context_window": "Published limit for the model family; admin-overridable.",
            "latency": "Observed average gateway latency, not a guarantee.",
            "tokens_per_second": "Observed output tokens divided by generation time.",
        },
    }


RANGES = {"1h": "-1 hours", "24h": "-24 hours", "7d": "-7 days", "30d": "-30 days"}
SORTS = {"time": "l.id", "latency": "l.latency_ms", "input": "l.prompt_tokens",
         "output": "l.completion_tokens", "ttft": "l.ttft_ms", "status": "l.status", "model": "l.model"}


def public_request_id(row_id: int) -> str:
    digest = hmac.new(SESSION_SECRET.encode(), str(row_id).encode(), hashlib.sha256).hexdigest()
    return f"req_{digest[:10]}"


def request_filters(request: Request) -> tuple[str, list[Any]]:
    params = request.query_params
    clauses = ["l.created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)"]
    values: list[Any] = [RANGES.get(params.get("range", "24h"), "-24 hours")]
    model = params.get("model", "").strip()[:200]
    if model:
        clauses.append("l.model = ?")
        values.append(model)
    status = params.get("status", "all")
    if status in {"2xx", "4xx", "5xx"}:
        low = int(status[0]) * 100
        clauses.append("l.status >= ? AND l.status < ?")
        values.extend([low, low + 100])
    search = params.get("q", "").strip()[:100]
    if search:
        clauses.append("l.model LIKE ? ESCAPE '\\'")
        values.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    return " AND ".join(clauses), values


@app.get("/requests/public")
async def public_requests(request: Request) -> Response:
    """Request telemetry only: model, status, tokens, timing. No bodies, keys, IPs or errors."""
    conn = request.app.state.db
    where, values = request_filters(request)
    params = request.query_params
    try:
        page = max(1, min(10_000, int(params.get("page", "1"))))
        size = max(5, min(100, int(params.get("size", "25"))))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="page and size must be integers") from error
    sort = SORTS.get(params.get("sort", "time"), "l.id")
    order = "ASC" if params.get("order") == "asc" else "DESC"
    base = f"FROM request_logs l WHERE {where}"
    columns = ("l.id, l.model, l.api_name, l.status, l.prompt_tokens, l.completion_tokens, "
               "l.total_tokens, l.latency_ms, l.ttft_ms, l.streamed, l.created_at")

    def shape(row: aiosqlite.Row) -> dict[str, Any]:
        speed = None
        gen_ms = (row["latency_ms"] or 0) - (row["ttft_ms"] or 0)
        if row["completion_tokens"] and gen_ms > 0:
            speed = round(row["completion_tokens"] / (gen_ms / 1000), 1)
        return {"id": public_request_id(row["id"]), "model": row["model"], "api": row["api_name"],
                "status": row["status"], "input_tokens": row["prompt_tokens"],
                "output_tokens": row["completion_tokens"], "total_tokens": row["total_tokens"],
                "latency_ms": row["latency_ms"], "ttft_ms": row["ttft_ms"],
                "streamed": bool(row["streamed"]), "tokens_per_second": speed,
                "created_at": row["created_at"]}

    if params.get("format") == "csv":
        async with conn.execute(f"SELECT {columns} {base} ORDER BY {sort} {order}, l.id DESC LIMIT 5000", values) as cur:
            rows = [shape(row) for row in await cur.fetchall()]
        header = ["id", "created_at", "model", "api", "status", "input_tokens", "output_tokens",
                  "total_tokens", "latency_ms", "ttft_ms", "tokens_per_second", "streamed"]

        def cell(value: Any) -> str:
            text = "" if value is None else str(value)
            if text[:1] in {"=", "+", "-", "@"}:
                text = "'" + text  # neutralise spreadsheet formula injection
            return '"' + text.replace('"', '""') + '"' if any(c in text for c in ',"\n') else text
        body = "\n".join([",".join(header), *(",".join(cell(r[k]) for k in header) for r in rows)])
        return Response(body, media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="rev9-requests.csv"'})

    async with conn.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM(l.total_tokens),0) AS tokens,
                   ROUND(AVG(l.latency_ms),1) AS avg_latency, ROUND(AVG(l.ttft_ms),1) AS avg_ttft,
                   SUM(CASE WHEN l.status < 400 THEN 1 ELSE 0 END) AS ok {base}""", values) as cur:
        summary = await cur.fetchone()
    async with conn.execute(f"SELECT {columns} {base} ORDER BY {sort} {order}, l.id DESC LIMIT ? OFFSET ?",
                            [*values, size, (page - 1) * size]) as cur:
        rows = [shape(row) for row in await cur.fetchall()]
    async with conn.execute(
        """SELECT DISTINCT model FROM request_logs
           WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days') AND model != ''
           ORDER BY model LIMIT 500""") as cur:
        model_names = [str(row["model"]) for row in await cur.fetchall()]
    total = int(summary["n"] or 0)
    return JSONResponse({
        "page": page, "size": size, "total": total,
        "summary": {"requests": total, "tokens": int(summary["tokens"] or 0),
                    "avg_latency_ms": summary["avg_latency"], "avg_ttft_ms": summary["avg_ttft"],
                    "success_rate": round(100 * (summary["ok"] or 0) / total, 1) if total else None},
        "models": model_names,
        "requests": rows,
    })


@app.get("/stats/public")
async def public_stats(request: Request) -> dict[str, Any]:
    """Aggregate live counters for the landing page."""
    conn = request.app.state.db
    async with conn.execute("SELECT name, value FROM gateway_counters") as cur:
        counters = {row["name"]: int(row["value"]) for row in await cur.fetchall()}
    async with conn.execute(
        """SELECT COALESCE(SUM(total_tokens),0) AS tokens, COUNT(*) AS n FROM request_logs
           WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-15 minutes')""") as cur:
        recent = await cur.fetchone()
    async with conn.execute(
        """SELECT CAST((julianday('now') - julianday(created_at)) * 24 AS INTEGER) AS bucket,
                  COUNT(*) AS n, COALESCE(SUM(total_tokens),0) AS tokens
           FROM request_logs WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-12 hours')
           GROUP BY bucket""") as cur:
        buckets = {int(row["bucket"]): row for row in await cur.fetchall()}
    enabled = await configured_model_ids(conn)
    async with conn.execute(
        """SELECT COUNT(*) AS enabled, SUM(CASE WHEN h.status='healthy' THEN 1 ELSE 0 END) AS healthy
           FROM providers p LEFT JOIN provider_health h ON h.provider_id=p.id WHERE p.enabled=1""") as cur:
        health = await cur.fetchone()
    series = [buckets.get(i) for i in range(11, -1, -1)]
    return {
        "tokens_processed": counters.get("tokens", 0),
        "requests": counters.get("requests", 0),
        "tokens_per_minute": round(int(recent["tokens"] or 0) / 15),
        "requests_per_minute": round(int(recent["n"] or 0) / 15, 1),
        "models": len(enabled),
        "routes_online": int(health["enabled"] or 0),
        "routes_healthy": int(health["healthy"] or 0),
        "series": {"requests": [int(b["n"]) if b else 0 for b in series],
                   "tokens": [int(b["tokens"]) if b else 0 for b in series]},
        "generated_at": now(),
    }


@app.post("/api/providers/{provider_id}/health")
async def check_provider_health(provider_id: int, request: Request) -> dict[str, Any]:
    dashboard_required(request)
    csrf_required(request)
    conn = request.app.state.db
    async with conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)) as cur:
        provider = await cur.fetchone()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    _, models_url = resolve_provider_urls(provider["chat_url"], provider["models_url"])
    started = time.monotonic()
    status, error = "healthy", ""
    try:
        response = await request.app.state.http.get(models_url, headers={provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}"})
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        status, error = "unhealthy", str(exc)[:500]
    latency = int((time.monotonic() - started) * 1000)
    async with request.app.state.db_lock:
        await conn.execute("""INSERT INTO provider_health(provider_id,status,latency_ms,checked_at,error)
          VALUES(?,?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET status=excluded.status,
          latency_ms=excluded.latency_ms,checked_at=excluded.checked_at,error=excluded.error""",
          (provider_id, status, latency, now(), error))
        await conn.commit()
    return {"provider_id": provider_id, "status": status, "latency_ms": latency, "error": error}


@app.get("/", include_in_schema=False)
async def landing() -> Response:
    return _frontend_index()


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Rev9 Apis · Admin</title><link rel="icon" href="/favicon.png"><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0A0A0B;color:#F5F5F3;font:15px/1.5 Inter,system-ui,-apple-system,sans-serif;padding:24px}
.card{width:100%;max-width:380px;background:#141416;border:1px solid #26262A;border-radius:12px;padding:28px}
.brand{font-weight:700;font-size:18px;letter-spacing:-.02em;margin:0 0 4px}.sub{color:#8E8E93;font-size:13px;margin:0 0 22px}
label{display:block;font-size:12px;color:#8E8E93;margin-bottom:6px}input{width:100%;background:#0A0A0B;color:#F5F5F3;border:1px solid #26262A;border-radius:8px;padding:11px 12px;font:inherit;outline:none}
input:focus{border-color:#F5F5F3}button{width:100%;margin-top:14px;background:#fff;color:#0A0A0B;border:0;border-radius:8px;padding:11px;font:inherit;font-weight:600;cursor:pointer}button:hover{background:#E6E6E3}
.err{background:rgba(240,106,106,.1);border:1px solid rgba(240,106,106,.35);color:#F06A6A;border-radius:8px;padding:9px 11px;font-size:13px;margin-bottom:14px}
.foot{margin-top:18px;font-size:12px;color:#5f5f66;text-align:center}a{color:#8E8E93}
</style></head><body><main class="card"><p class="brand">Rev9 Apis</p><p class="sub">Gateway control center</p>__ERROR__<form method="post" action="/rev9" autocomplete="off"><input type="hidden" name="csrf" value="__CSRF__"><label for="pw">Dashboard password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required maxlength="256"><button type="submit">Sign in</button></form><p class="foot"><a href="/">Back to site</a></p></main></body></html>"""

LOGIN_ERRORS = {
    "invalid": "Incorrect password.",
    "locked": "Too many attempts. Try again in 15 minutes.",
    "expired": "Your form expired. Please try again.",
}


@app.get("/rev9", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> Response:
    if not request.session.get("dashboard"):
        token = request.session.get("login_csrf") or secrets.token_urlsafe(32)
        request.session["login_csrf"] = token
        message = LOGIN_ERRORS.get(request.query_params.get("e", ""), "")
        error_html = f'<div class="err" role="alert">{html.escape(message)}</div>' if message else ""
        return HTMLResponse(LOGIN_HTML.replace("__CSRF__", token).replace("__ERROR__", error_html))
    issued = float(request.session.get("issued", 0) or 0)
    if time.time() - issued > SESSION_MAX_AGE:
        request.session.clear()
        return RedirectResponse("/rev9", status_code=303)
    return HTMLResponse(DASHBOARD_HTML.replace("__CSRF_TOKEN__", request.session.get("csrf", "")))


@app.post("/rev9")
async def login(request: Request) -> RedirectResponse:
    ip = client_ip(request)
    if len(_auth_failures.get(ip, [])) >= LOGIN_MAX_ATTEMPTS:
        recent = [t for t in _auth_failures[ip] if time.monotonic() - t < 900]
        _auth_failures[ip] = recent
        if len(recent) >= LOGIN_MAX_ATTEMPTS:
            return RedirectResponse("/rev9?e=locked", status_code=303)
    form = await request.form()
    expected = request.session.get("login_csrf", "")
    if not expected or not hmac.compare_digest(str(form.get("csrf", "")), expected):
        return RedirectResponse("/rev9?e=expired", status_code=303)
    password = str(form.get("password", ""))[:256]
    if not hmac.compare_digest(hash_key(password), hash_key(ADMIN_PASSWORD)):
        _auth_failures.setdefault(ip, []).append(time.monotonic())
        logging.getLogger("uvicorn.error").warning("rev9 login failed")
        await asyncio.sleep(0.6)  # slow down online guessing
        return RedirectResponse("/rev9?e=invalid", status_code=303)
    _auth_failures.pop(ip, None)
    request.session.clear()  # rotate session contents on privilege change
    request.session["dashboard"] = True
    request.session["issued"] = time.time()
    request.session["csrf"] = secrets.token_urlsafe(32)
    return RedirectResponse("/rev9", status_code=303)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    form = await request.form()
    expected = request.session.get("csrf", "")
    if expected and hmac.compare_digest(str(form.get("csrf", "")), expected):
        request.session.clear()
    return RedirectResponse("/rev9", status_code=303)


@app.get("/api/model-specs")
async def list_model_specs(request: Request) -> dict[str, Any]:
    dashboard_required(request)
    conn = request.app.state.db
    async with conn.execute("SELECT model, context_window, max_output_tokens FROM model_specs") as cur:
        overrides = {str(row["model"]): dict(row) for row in await cur.fetchall()}
    items = []
    for model_id in dict.fromkeys([*await configured_model_ids(conn), *overrides.keys()]):
        context, max_output, kind = default_spec(model_id)
        override = overrides.get(model_id, {})
        items.append({"model": model_id, "type": kind, "default_context": context,
                      "default_max_output": max_output,
                      "context_window": override.get("context_window"),
                      "max_output_tokens": override.get("max_output_tokens")})
    return {"models": items}


@app.put("/api/model-specs")
async def save_model_spec(request: Request) -> dict[str, Any]:
    dashboard_required(request)
    csrf_required(request)
    data = await request_json_object(request)
    model = str(data.get("model", "")).strip()
    if not model or len(model) > 200:
        raise HTTPException(status_code=400, detail="model is required")

    def limit(name: str) -> int | None:
        value = data.get(name)
        if value in (None, "", 0):
            return None
        if type(value) is not int or not 1 <= value <= 100_000_000:
            raise HTTPException(status_code=400, detail=f"{name} must be a positive integer")
        return value
    context, max_output = limit("context_window"), limit("max_output_tokens")
    conn = request.app.state.db
    async with request.app.state.db_lock:
        if context is None and max_output is None:
            await conn.execute("DELETE FROM model_specs WHERE model=?", (model,))
        else:
            await conn.execute(
                """INSERT INTO model_specs(model,context_window,max_output_tokens,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(model) DO UPDATE SET context_window=excluded.context_window,
                   max_output_tokens=excluded.max_output_tokens, updated_at=excluded.updated_at""",
                (model, context, max_output, now()))
        await conn.commit()
    return {"model": model, "context_window": context, "max_output_tokens": max_output}


@app.get("/api/providers")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    dashboard_required(request)
    conn = request.app.state.db
    async with conn.execute("SELECT * FROM providers ORDER BY id DESC") as cur:
        rows = await cur.fetchall()
    return [safe_provider(row) for row in rows]


@app.post("/api/providers")
async def create_provider(request: Request) -> JSONResponse:
    dashboard_required(request)
    csrf_required(request)
    data = await request_json_object(request)
    name, chat_url, upstream_key = (str(data.get(field, "")).strip() for field in ("name", "chat_url", "upstream_api_key"))
    if not name or not valid_url(chat_url) or not upstream_key:
        scheme_hint = "http(s)" if ALLOW_INSECURE_UPSTREAMS else "https"
        raise HTTPException(status_code=400, detail=f"Name, valid {scheme_hint} chat URL, and upstream API key are required")
    models_url = str(data.get("models_url", "")).strip()
    chat_url, models_url = resolve_provider_urls(chat_url, models_url)
    if models_url and not valid_url(models_url):
        raise HTTPException(status_code=400, detail="Models URL must use an allowed http(s) scheme")
    auth_header = str(data.get("auth_header", "Authorization")).strip()
    auth_prefix = str(data.get("auth_prefix", "Bearer")).strip()
    if not auth_header or any(char in auth_header for char in "\r\n:") or any(char in auth_prefix for char in "\r\n"):
        raise HTTPException(status_code=400, detail="Invalid upstream authentication header")
    public_id, key = uuid.uuid4().hex, f"mgw_{secrets.token_urlsafe(32)}"
    conn = request.app.state.db
    async with request.app.state.db_lock:
        cursor = await conn.execute(
            """INSERT INTO providers
              (name,public_id,chat_url,models_url,encrypted_key,auth_header,auth_prefix,client_key_hash,created_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, public_id, chat_url, models_url, encrypt(upstream_key), auth_header, auth_prefix, hash_key(key), now()),
        )
        await conn.commit()
        async with conn.execute("SELECT * FROM providers WHERE id = ?", (cursor.lastrowid,)) as cur:
            row = await cur.fetchone()
    result = safe_provider(row)
    result["client_key"] = key  # Displayed only at creation; save it now.
    return JSONResponse(result, status_code=201)


@app.patch("/api/providers/{provider_id}")
async def update_provider(provider_id: int, request: Request) -> dict[str, Any]:
    dashboard_required(request)
    csrf_required(request)
    data = await request_json_object(request)
    allowed = data.get("allowed_models", [])
    if (not isinstance(allowed, list) or len(allowed) > 10_000 or
            not all(isinstance(item, str) and 0 < len(item.strip()) <= 200 for item in allowed)):
        raise HTTPException(status_code=400, detail="allowed_models must be an array of model IDs")
    allowed = list(dict.fromkeys(item.strip() for item in allowed))
    conn = request.app.state.db
    async with request.app.state.db_lock:
        async with conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Provider not found")
        await conn.execute(
            "UPDATE providers SET allowed_models=?, enabled=? WHERE id=?",
            (json.dumps(allowed), int(bool(data.get("enabled", row["enabled"]))), provider_id),
        )
        await conn.commit()
        async with conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)) as cur:
            return safe_provider(await cur.fetchone())


@app.delete("/api/providers/{provider_id}")
async def delete_provider(provider_id: int, request: Request) -> dict[str, Any]:
    """Permanently remove an endpoint and its associated local telemetry."""
    dashboard_required(request)
    csrf_required(request)
    conn = request.app.state.db
    async with request.app.state.db_lock:
        async with conn.execute("SELECT id FROM providers WHERE id=?", (provider_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Provider not found")
        await conn.execute("DELETE FROM request_logs WHERE provider_id=?", (provider_id,))
        await conn.execute("DELETE FROM provider_health WHERE provider_id=?", (provider_id,))
        await conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
        await conn.commit()
    return {"deleted": True, "provider_id": provider_id}


@app.post("/api/providers/{provider_id}/rotate-key")
async def rotate_key(provider_id: int, request: Request) -> dict[str, str]:
    dashboard_required(request)
    csrf_required(request)
    key = f"mgw_{secrets.token_urlsafe(32)}"
    conn = request.app.state.db
    async with request.app.state.db_lock:
        async with conn.execute("SELECT 1 FROM providers WHERE id=?", (provider_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Provider not found")
        await conn.execute("UPDATE providers SET client_key_hash=? WHERE id=?", (hash_key(key), provider_id))
        await conn.commit()
    return {"client_key": key}


@app.post("/api/providers/{provider_id}/discover-models")
async def discover_models(provider_id: int, request: Request) -> dict[str, list[str]]:
    dashboard_required(request)
    csrf_required(request)
    conn = request.app.state.db
    async with conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)) as cur:
        provider = await cur.fetchone()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    _, models_url = resolve_provider_urls(provider["chat_url"], provider["models_url"])
    if not models_url:
        raise HTTPException(status_code=400, detail="Set a models URL for this provider")
    headers = {provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}".strip()}
    try:
        response = await request.app.state.http.get(models_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        models = [item["id"] for item in data.get("data", []) if isinstance(item, dict) and item.get("id")]
        return {"models": models}
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise HTTPException(status_code=502, detail=f"Unable to fetch models: {error}") from error


@app.post("/api/providers/{provider_id}/discover-media-models")
async def discover_media_models(provider_id: int, request: Request) -> dict[str, list[str]]:
    dashboard_required(request)
    csrf_required(request)
    conn = request.app.state.db
    async with conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)) as cur:
        provider = await cur.fetchone()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    _, models_url = resolve_provider_urls(provider["chat_url"], provider["models_url"])
    if not models_url:
        raise HTTPException(status_code=400, detail="Set a models URL for this provider")
    headers = {provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}".strip()}
    try:
        response = await request.app.state.http.get(models_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        models = [item["id"] for item in data.get("data", []) if isinstance(item, dict) and item.get("id")]
        media_models = [model for model in models if any(word in model.lower() for word in ("image", "tts", "speech", "audio", "video", "veo", "voice"))]
        return {"models": media_models or models}
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise HTTPException(status_code=502, detail=f"Unable to fetch media models") from error


@app.get("/api/usage")
async def usage(request: Request) -> dict[str, Any]:
    dashboard_required(request)
    conn = request.app.state.db
    async with conn.execute("SELECT COUNT(*) requests, COALESCE(SUM(total_tokens),0) tokens FROM request_logs") as cur:
        totals = await cur.fetchone()
    async with conn.execute("""SELECT l.id, l.provider_id, l.model, l.api_name, l.status, l.prompt_tokens,
      l.completion_tokens, l.total_tokens, l.latency_ms, l.error, l.created_at,
      p.name AS provider_name
      FROM request_logs l LEFT JOIN providers p ON p.id = l.provider_id
      ORDER BY l.id DESC LIMIT 100""") as cur:
        logs = await cur.fetchall()
    async with conn.execute("""SELECT provider_id, model, COUNT(*) requests,
      COALESCE(SUM(total_tokens),0) tokens, ROUND(AVG(latency_ms),1) avg_latency_ms,
      SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) errors
      FROM request_logs GROUP BY provider_id, model ORDER BY requests DESC""") as cur:
        breakdown = await cur.fetchall()
    return {"requests": totals["requests"], "tokens": totals["tokens"],
            "breakdown": [dict(row) for row in breakdown], "logs": [dict(row) for row in logs]}


def native_usage(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    def count(name: str) -> int:
        try:
            return max(0, int(usage.get(name, 0) or 0))
        except (ValueError, TypeError, OverflowError):
            return 0
    prompt = count("input_tokens")
    completion = count("output_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


async def native_protocol(public_id: str, request: Request, protocol: str) -> Response:
    provider = await public_provider(public_id, request)
    payload = await request_json_object(request)
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise HTTPException(400, "model must be a non-empty string up to 200 characters")
    if model not in json.loads(provider["allowed_models"]):
        raise HTTPException(403, "This model is not enabled for the endpoint")
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise HTTPException(400, "stream must be a boolean")
    headers = {"Accept": "text/event-stream" if stream else "application/json"}
    key = decrypt(provider["encrypted_key"])
    if protocol == "messages":
        version = request.headers.get("anthropic-version", "")
        if not version or len(version) > 32:
            raise HTTPException(400, "anthropic-version header is required")
        if type(payload.get("max_tokens")) is not int or payload["max_tokens"] <= 0:
            raise HTTPException(400, "max_tokens must be a positive integer")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            raise HTTPException(400, f"messages must contain 1 to {MAX_MESSAGES} items")
        if not all(isinstance(item, dict) and item.get("role") in {"user", "assistant"} for item in messages):
            raise HTTPException(400, "Each message must have a user or assistant role")
        headers.update({"x-api-key": key, "anthropic-version": version})
        if request.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = request.headers["anthropic-beta"]
    else:
        if not isinstance(payload.get("input"), (str, list)):
            raise HTTPException(400, "input must be a string or array")
        if "instructions" in payload and not isinstance(payload["instructions"], str):
            raise HTTPException(400, "instructions must be a string")
        headers["Authorization"] = f"Bearer {key}"

    base = provider["chat_url"].rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/responses"):
        if base.endswith(suffix):
            base = base.removesuffix(suffix)
            break
    if not base.endswith("/v1"):
        base += "/v1"
    app = request.app
    started = time.monotonic()
    api_name = "anthropic" if protocol == "messages" else "codex"
    try:
        await asyncio.wait_for(app.state.upstream_slots.acquire(), timeout=QUEUE_TIMEOUT)
    except TimeoutError:
        raise HTTPException(503, "Gateway is busy. Please retry shortly.")
    upstream = None
    handed_off = False
    status = 502
    metered = {}
    try:
        upstream = await app.state.http.send(
            app.state.http.build_request("POST", f"{base}/{protocol}", headers=headers, json=payload),
            stream=True,
        )
        status = upstream.status_code
        if status >= 400:
            return masked_upstream_error(status)
        if not stream:
            body = await upstream.aread()
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    metered = native_usage(data)
            except (ValueError, TypeError):
                pass
            return Response(body, status_code=status, media_type="application/json")

        async def relay():
            pending = b""
            counters = {}
            stream_status = status
            first_token: int | None = None
            try:
                async for chunk in upstream.aiter_bytes():
                    if first_token is None:
                        first_token = int((time.monotonic() - started) * 1000)
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if not line.startswith(b"data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                            for obj in (event, event.get("message", {}), event.get("response", {})):
                                if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                                    counters.update(obj["usage"])
                        except (ValueError, AttributeError):
                            pass
                    if len(pending) > MAX_REQUEST_BYTES:
                        pending = b""
                    yield chunk
            except httpx.RequestError:
                stream_status = 502
            finally:
                await upstream.aclose()
                app.state.upstream_slots.release()
                await record(app, provider["id"], model, stream_status, started,
                             native_usage({"usage": counters}), api_name=api_name,
                             ttft_ms=first_token, streamed=True)

        response = StreamingResponse(relay(), status_code=status, media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        handed_off = True
        return response
    except httpx.TimeoutException:
        status = 504
        raise HTTPException(504, "The model request timed out. Please retry.")
    except httpx.RequestError:
        status = 502
        raise HTTPException(502, "Could not reach upstream provider")
    finally:
        if not handed_off:
            try:
                if upstream is not None:
                    await upstream.aclose()
            finally:
                app.state.upstream_slots.release()
                await record(app, provider["id"], model, status, started, metered, api_name=api_name)


async def anthropic_messages(public_id: str, request: Request) -> Response:
    return await native_protocol(public_id, request, "messages")


async def codex_responses(public_id: str, request: Request) -> Response:
    return await native_protocol(public_id, request, "responses")


async def record(app: FastAPI, provider_id: int, model: str, status: int, started: float,
                 usage: dict[str, Any] | None = None, error: str = "", api_name: str = "chat",
                 ttft_ms: int | None = None, streamed: bool = False) -> None:
    usage = usage or {}
    conn = app.state.db
    try:
        def count(*names: str) -> int:
            for name in names:
                try:
                    value = int(usage.get(name, 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    value = 0
                if value:
                    return max(0, value)
            return 0
        prompt = count("prompt_tokens", "input_tokens")
        completion = count("completion_tokens", "output_tokens")
        total = count("total_tokens") or prompt + completion
        async with app.state.db_lock:
            await conn.execute(
                "UPDATE gateway_counters SET value = value + CASE name WHEN 'requests' THEN 1 ELSE ? END",
                (total,),
            )
            await conn.execute(
                """INSERT INTO request_logs(provider_id,model,api_name,status,prompt_tokens,completion_tokens,total_tokens,latency_ms,error,created_at,ttft_ms,streamed)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (provider_id, model, api_name, status, prompt, completion, total,
                 int((time.monotonic() - started) * 1000), error[:500], now(), ttft_ms, int(streamed)),
            )
            await conn.commit()
    except Exception:  # a logging failure must never crash the proxy path
        pass


def masked_upstream_error(status: int) -> JSONResponse:
    """Return a provider-neutral error without forwarding upstream details."""
    if status == 400:
        message, code, public_status = "The request was rejected by the configured model.", "invalid_request", 400
    elif status in {401, 403}:
        message, code, public_status = "The configured model is temporarily unavailable.", "upstream_unavailable", 502
    elif status == 429:
        message, code, public_status = "The model is currently rate limited. Please retry later.", "rate_limited", 429
    elif status in {408, 504}:
        message, code, public_status = "The model request timed out. Please retry.", "upstream_timeout", 504
    else:
        message, code, public_status = "The configured model returned an error.", "upstream_error", 502
    return JSONResponse(
        status_code=public_status,
        content={"error": {"message": message, "type": "gateway_error", "code": code}},
    )


@app.get("/v1/models")
async def clean_models(request: Request) -> dict[str, Any]:
    provider = await provider_for_clean_v1(request)
    return {"object": "list", "data": [{"id": model, "object": "model"} for model in json.loads(provider["allowed_models"])]}


async def image_generations(public_id: str, request: Request) -> Response:
    return await media_request(public_id, request, "image", "images/generations")


async def audio_speech(public_id: str, request: Request) -> Response:
    return await media_request(public_id, request, "speech", "audio/speech", "binary")


async def video_generations(public_id: str, request: Request) -> Response:
    return await media_request(public_id, request, "video", "videos/generations")


@app.get("/v1/videos/{operation_id}")
async def clean_video_operation(operation_id: str, request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await video_operation(provider["public_id"], operation_id, request)


async def video_operation(public_id: str, operation_id: str, request: Request) -> Response:
    """Public, provider-neutral status for a media operation.

    The upstream operation identifier is used server-side only and is never
    reflected in the response; output URLs always point at gateway media routes.
    """
    provider = await public_provider(public_id, request)
    conn = request.app.state.db
    async with conn.execute("SELECT * FROM media_operations WHERE id=? AND provider_id=?", (operation_id, provider["id"])) as cur:
        operation = await cur.fetchone()
    if not operation:
        raise HTTPException(status_code=404, detail="Video operation not found or expired")
    result = await public_operation_payload(request.app, request, public_id, operation_id, provider, operation)
    return JSONResponse(result)


@app.get("/media/{asset_id}")
async def media_asset(asset_id: str, request: Request) -> Response:
    conn = request.app.state.db
    async with conn.execute("SELECT body,media_type FROM media_blobs WHERE id=?", (asset_id,)) as cur:
        blob = await cur.fetchone()
    if blob:
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(blob["media_type"], "bin")
        return Response(bytes(blob["body"]), media_type=blob["media_type"],
                        headers={"Content-Disposition": f'inline; filename="gateway-image-{asset_id}.{extension}"'})
    async with conn.execute("SELECT body FROM video_outputs WHERE operation_id=?", (asset_id,)) as cur:
        video = await cur.fetchone()
    if video:
        return Response(bytes(video["body"]), media_type="video/mp4",
                        headers={"Content-Disposition": f'inline; filename="gateway-video-{asset_id}.mp4"'})
    async with conn.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)) as cur:
        asset = await cur.fetchone()
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found or expired")
    try:
        async with request.app.state.upstream_slots:
            upstream = await request.app.state.http.get(asset["source_url"])
        upstream.raise_for_status()
        body = await upstream.aread()
        content_type = upstream.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        extension = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4", "audio/mpeg": "mp3"}.get(content_type, "bin")
        return Response(body, media_type=content_type, headers={"Content-Disposition": f'inline; filename="{neutral_filename(asset["media_type"], extension)}"'})
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Media asset unavailable") from error
    finally:
        try:
            await upstream.aclose()
        except UnboundLocalError:
            pass


def _extract_usage_from_sse(raw: bytes) -> dict[str, Any]:
    """Parse the trailing usage chunk from an SSE stream, if present."""
    usage: dict[str, Any] = {}
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload in (b"", b"[DONE]"):
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
    return usage


async def gateway_chat(public_id: str, request: Request) -> Response:
    api_name = getattr(request.state, "api_name", "chat")
    provider = await public_provider(public_id, request)
    payload = await request_json_object(request)
    model, is_stream = validate_chat_payload(payload)
    if model not in json.loads(provider["allowed_models"]):
        raise HTTPException(status_code=403, detail="This model is not enabled for the endpoint")

    # Ask the upstream to include a final usage chunk so streaming calls are metered too.
    if is_stream:
        opts = payload.get("stream_options")
        payload["stream_options"] = {**opts, "include_usage": True} if isinstance(opts, dict) else {"include_usage": True}

    headers = {"Accept": "application/json",
               provider["auth_header"]: f"{provider['auth_prefix']} {decrypt(provider['encrypted_key'])}".strip()}
    chat_url, _ = resolve_provider_urls(provider["chat_url"], provider["models_url"])
    client: httpx.AsyncClient = request.app.state.http
    app = request.app
    started = time.monotonic()
    slot_acquired = False
    try:
        try:
            await asyncio.wait_for(app.state.upstream_slots.acquire(), timeout=QUEUE_TIMEOUT)
            slot_acquired = True
        except TimeoutError as error:
            await record(app, provider["id"], model, 503, started, api_name=api_name, error="gateway concurrency limit reached")
            raise HTTPException(status_code=503, detail="Gateway is busy. Please retry shortly.") from error
        upstream = await client.send(
            client.build_request(
                "POST", chat_url, headers=headers, json=payload,
                extensions={"timeout": {"connect": CONNECT_TIMEOUT, "read": TIMEOUT, "write": TIMEOUT, "pool": QUEUE_TIMEOUT}},
            ),
            stream=is_stream,
        )
    except httpx.TimeoutException as error:
        if slot_acquired:
            app.state.upstream_slots.release()
        await record(app, provider["id"], model, 504, started, api_name=api_name, error="upstream connection timed out")
        raise HTTPException(status_code=504, detail="The model request timed out. Please retry.") from error
    except httpx.RequestError as error:
        if slot_acquired:
            app.state.upstream_slots.release()
        await record(app, provider["id"], model, 502, started, api_name=api_name, error="upstream request failed")
        raise HTTPException(status_code=502, detail="Could not reach upstream provider") from error

    if upstream.status_code >= 400:
        try:
            await upstream.aread()  # Do not expose or retain the provider's error response.
        except httpx.RequestError:
            pass
        finally:
            try:
                await upstream.aclose()
            finally:
                app.state.upstream_slots.release()
        await record(app, provider["id"], model, upstream.status_code, started, api_name=api_name, error=f"upstream HTTP {upstream.status_code}")
        return masked_upstream_error(upstream.status_code)

    if is_stream:
        # Tee the stream to the client while accumulating the trailing usage chunk.
        pid, status = provider["id"], upstream.status_code
        collected: list[bytes] = []
        log_status, log_error = status, ""

        first_token: list[int] = []

        async def relay() -> Any:
            nonlocal log_status, log_error
            try:
                async for chunk in upstream.aiter_raw():
                    if not first_token:
                        first_token.append(int((time.monotonic() - started) * 1000))
                    collected.append(chunk)
                    yield chunk
            except httpx.TimeoutException:
                # The HTTP status is already 200 once an SSE stream has started, so
                # report a neutral OpenAI-style error event rather than letting the
                # exception abort the ASGI response and produce a server traceback.
                log_status, log_error = 504, "upstream stream timed out"
                error_event = {
                    "error": {
                        "message": "The model stream timed out. Please retry.",
                        "type": "gateway_error",
                        "code": "upstream_timeout",
                    }
                }
                yield f"data: {json.dumps(error_event, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode()
            except httpx.HTTPError:
                log_status, log_error = 502, "upstream stream interrupted"
                error_event = {
                    "error": {
                        "message": "The model stream was interrupted. Please retry.",
                        "type": "gateway_error",
                        "code": "upstream_error",
                    }
                }
                yield f"data: {json.dumps(error_event, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode()
            finally:
                try:
                    await upstream.aclose()
                finally:
                    app.state.upstream_slots.release()
                usage = _extract_usage_from_sse(b"".join(collected[-8:]))  # usage is in the last chunks
                await record(app, pid, model, log_status, started, usage, log_error, api_name=api_name,
                             ttft_ms=first_token[0] if first_token else None, streamed=True)

        return StreamingResponse(
            relay(), status_code=status,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    try:
        body = await upstream.aread()
    except httpx.TimeoutException as error:
        await record(app, provider["id"], model, 504, started, api_name=api_name, error="upstream response timed out")
        raise HTTPException(status_code=504, detail="The model request timed out. Please retry.") from error
    except httpx.RequestError as error:
        await record(app, provider["id"], model, 502, started, api_name=api_name, error="upstream response interrupted")
        raise HTTPException(status_code=502, detail="The model response was interrupted. Please retry.") from error
    finally:
        try:
            await upstream.aclose()
        finally:
            app.state.upstream_slots.release()
    try:
        response_data = json.loads(body)
        usage_data = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
    except (ValueError, TypeError):
        usage_data = {}
    await record(app, provider["id"], model, upstream.status_code, started, usage_data, api_name=api_name)
    return Response(content=body, status_code=upstream.status_code, media_type=upstream.headers.get("content-type", "application/json"))


@app.post("/v1/chat/completions")
async def clean_chat(request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await gateway_chat(provider["public_id"], request)


@app.post("/v1/responses")
async def clean_responses(request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await codex_responses(provider["public_id"], request)


@app.post("/v1/messages")
async def clean_messages(request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await anthropic_messages(provider["public_id"], request)


@app.post("/v1/images/generations")
async def clean_images(request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await image_generations(provider["public_id"], request)


@app.post("/v1/audio/speech")
async def clean_speech(request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await audio_speech(provider["public_id"], request)


@app.post("/v1/videos/generations")
async def clean_video(request: Request) -> Response:
    provider = await provider_for_clean_v1(request)
    return await video_generations(provider["public_id"], request)



DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rev9 Apis · Admin</title>
<meta name="robots" content="noindex,nofollow">
<style>
  :root{
    --bg:#0A0A0B; --panel:#141416; --panel-2:#1B1B1E; --line:#26262A;
    --line-2:#323237; --txt:#F5F5F3; --muted:#8E8E93; --muted-2:#5f5f66;
    --accent:#F5F5F3; --accent-2:#E6E6E3; --good:#3DDC97; --good-dim:#1c6b4d;
    --warn:#F5B544; --bad:#F06A6A; --radius:14px; --radius-sm:9px;
    --shadow:0 8px 30px rgba(0,0,0,.35);
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
    background:var(--bg);
    color:var(--txt); -webkit-font-smoothing:antialiased; min-height:100vh;
  }
  .wrap{max-width:1440px; margin:0 auto; padding:0 28px 36px}
  .console-shell{display:grid;grid-template-columns:220px minmax(0,1fr);gap:28px;align-items:start}
  .console-main{min-width:0}
  .sidebar{position:sticky;top:18px;align-self:start;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:12px;box-shadow:var(--shadow)}
  .sidebar-title{padding:12px 11px 16px;color:var(--muted);font-size:10px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase}
  .sidebar-foot{margin:16px 8px 4px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px;line-height:1.5}
  .console-nav{display:flex;flex-direction:column;gap:4px;margin:0;padding:0;background:transparent;border:0}
  .console-nav button{display:flex;align-items:center;gap:10px;width:100%;border:0;background:transparent;color:var(--muted);padding:11px 12px;border-radius:10px;font:inherit;font-weight:650;text-align:left;cursor:pointer;transition:.15s}
  .console-nav button::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.55}
  .console-nav button:hover,.console-nav button.active{background:var(--panel-2);color:var(--txt)}
  .console-nav button.active::before{background:var(--good);opacity:1}
  a{color:var(--accent); text-decoration:none}

  /* Top bar */
  header{
    display:flex; align-items:center; gap:14px; padding:22px 0 18px;
    position:sticky; top:0; z-index:20;
    background:linear-gradient(var(--bg) 70%,transparent);
  }
  .logo{
    width:38px; height:38px; border-radius:10px; flex:none;
    background:#fff;
    display:grid; place-items:center; font-weight:800; color:#0A0A0B;
  }
  .brand h1{font-size:17px; margin:0; letter-spacing:.2px}
  .brand p{font-size:12px; margin:0; color:var(--muted)}
  header .spacer{flex:1}

  /* Buttons */
  .btn{
    font:inherit; font-weight:600; cursor:pointer; border:1px solid var(--line-2);
    background:var(--panel-2); color:var(--txt); padding:9px 15px;
    border-radius:var(--radius-sm); transition:.15s; white-space:nowrap;
  }
  .btn:hover{border-color:#5f5f66; background:#222226}
  .btn:active{transform:translateY(1px)}
  .btn.primary{background:#fff; border-color:#fff; color:#0A0A0B}
  .btn.primary:hover{background:#E6E6E3; border-color:#E6E6E3}
  .btn.ghost{background:transparent}
  .btn.sm{padding:6px 11px; font-size:12.5px}
  .btn.danger:hover{border-color:var(--bad); color:var(--bad)}

  /* Stats */
  .stats{display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:6px 0 24px}
  .stat{
    background:var(--panel); border:1px solid var(--line); border-radius:var(--radius);
    padding:16px 18px;
  }
  .stat .label{font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.6px}
  .stat .value{font-size:26px; font-weight:700; margin-top:6px; letter-spacing:-.5px}
  .stat .value small{font-size:13px; color:var(--muted); font-weight:500}

  /* Panels */
  .panel{
    background:var(--panel); border:1px solid var(--line); border-radius:var(--radius);
    margin:20px 0; overflow:hidden; box-shadow:var(--shadow);
  }
  .panel > .head{
    display:flex; align-items:center; gap:10px; padding:16px 20px;
    border-bottom:1px solid var(--line);
  }
  .panel .head h2{font-size:15px; margin:0}
  .panel .head .hint{font-size:12.5px; color:var(--muted); margin-left:auto}
  .panel .body{padding:20px}

  /* Form */
  .form-grid{display:grid; grid-template-columns:1fr 1fr; gap:14px}
  .field{display:flex; flex-direction:column; gap:6px}
  .field.full{grid-column:1 / -1}
  .field label{font-size:12px; color:var(--muted); font-weight:600}
  input,select,textarea{
    font:inherit; color:var(--txt); background:var(--panel-2);
    border:1px solid var(--line-2); border-radius:var(--radius-sm); padding:10px 12px;
    width:100%; transition:.15s; outline:none;
  }
  input:focus,select:focus,textarea:focus{border-color:#8E8E93; box-shadow:0 0 0 3px rgba(255,255,255,.08)}
  input::placeholder{color:var(--muted-2)}
  .form-actions{display:flex; justify-content:flex-end; margin-top:16px}

  /* Provider cards */
  .empty{color:var(--muted); text-align:center; padding:34px 0}
  .prov{border:1px solid var(--line); border-radius:var(--radius); margin-bottom:14px; background:var(--panel-2)}
  .prov:last-child{margin-bottom:0}
  .prov .top{display:flex; align-items:center; gap:12px; padding:15px 18px; cursor:pointer}
  .prov .top:hover{background:#202024}
  .prov .name{font-weight:700; font-size:15px}
  .badge{
    font-size:11px; font-weight:700; padding:3px 9px; border-radius:99px;
    display:inline-flex; align-items:center; gap:5px;
  }
  .badge.on{background:rgba(55,211,153,.14); color:var(--good)}
  .badge.off{background:rgba(138,151,171,.14); color:var(--muted)}
  .dot{width:7px; height:7px; border-radius:99px; background:currentColor}
  .prov .meta{margin-left:auto; color:var(--muted); font-size:12.5px}
  .chev{transition:.2s; color:var(--muted)}
  .prov.open .chev{transform:rotate(90deg)}

  .prov .detail{display:none; padding:0 18px 18px; border-top:1px solid var(--line)}
  .prov.open .detail{display:block}
  .row{display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:14px}
  .url-box{
    display:flex; align-items:center; gap:8px; background:var(--bg);
    border:1px solid var(--line-2); border-radius:var(--radius-sm); padding:8px 10px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
    overflow:auto; flex:1; min-width:0;
  }
  .url-box{display:block; min-width:0; flex:1; overflow:hidden}
  .url-box code{display:block; white-space:normal; overflow-wrap:anywhere; word-break:break-word; color:#E6E6E3; line-height:1.8}
  .model-list{display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:7px; max-height:230px; overflow:auto; padding:10px; background:var(--bg); border:1px solid var(--line-2); border-radius:var(--radius-sm)}
  .model-option{display:flex; align-items:center; gap:8px; min-width:0; padding:5px 7px; border-radius:6px; cursor:pointer; color:var(--txt)}
  .model-option:hover{background:#222226}
  .model-option input{width:auto; accent-color:var(--accent); margin:0; flex:none}
  .model-option span{overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px}
  .response{margin:14px 0 0; min-height:100px; max-height:380px; overflow:auto; padding:12px; background:var(--bg); border:1px solid var(--line-2); border-radius:var(--radius-sm); color:#E6E6E3; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap}
  .subtle{font-size:12px; color:var(--muted); margin:14px 0 6px}
  .actions{display:flex; gap:8px; flex-wrap:wrap; margin-top:14px}

  /* Logs table */
  .table-wrap{overflow:auto; border:1px solid var(--line); border-radius:var(--radius-sm)}
  table{width:100%; border-collapse:collapse; font-size:12.5px}
  th,td{text-align:left; padding:9px 12px; white-space:nowrap}
  thead th{background:var(--panel-2); color:var(--muted); font-weight:600; position:sticky; top:0}
  tbody tr{border-top:1px solid var(--line)}
  tbody tr:hover{background:var(--panel-2)}
  td.mono{font-family:ui-monospace,monospace; color:var(--muted)}
  .pill{font-size:11px; font-weight:700; padding:2px 8px; border-radius:99px}
  .pill.ok{background:rgba(55,211,153,.14); color:var(--good)}
  .pill.err{background:rgba(255,92,108,.14); color:var(--bad)}
  .err-cell{color:var(--bad); max-width:280px; overflow:hidden; text-overflow:ellipsis}

  /* Toasts */
  #toasts{position:fixed; right:18px; bottom:18px; display:flex; flex-direction:column; gap:10px; z-index:100}
  .toast{
    background:var(--panel-2); border:1px solid var(--line-2); border-left:3px solid var(--accent);
    padding:12px 15px; border-radius:var(--radius-sm); box-shadow:var(--shadow);
    max-width:360px; animation:slide .25s ease; font-size:13px;
  }
  .toast.good{border-left-color:var(--good)}
  .toast.bad{border-left-color:var(--bad)}
  .toast b{display:block; margin-bottom:2px}
  .toast .k{font-family:ui-monospace,monospace; word-break:break-all; color:var(--good); font-size:12px; margin-top:6px}
  @keyframes slide{from{opacity:0; transform:translateX(20px)}}

  /* Modal */
  .modal-bg{position:fixed; inset:0; background:rgba(5,7,12,.7); display:none; place-items:center; z-index:90; padding:20px}
  .modal-bg.show{display:grid}
  .modal{background:var(--panel); border:1px solid var(--line-2); border-radius:var(--radius); max-width:440px; width:100%; box-shadow:var(--shadow)}
  .modal .head{padding:16px 20px; border-bottom:1px solid var(--line); font-weight:700}
  .modal .body{padding:20px; color:var(--muted); line-height:1.6}
  .modal .foot{display:flex; justify-content:flex-end; gap:10px; padding:14px 20px; border-top:1px solid var(--line)}

  .skeleton{height:60px; border-radius:var(--radius); background:linear-gradient(90deg,var(--panel-2) 25%,#26262A 50%,var(--panel-2) 75%); background-size:200% 100%; animation:sk 1.2s infinite; margin-bottom:12px}
  @keyframes sk{from{background-position:200% 0}to{background-position:-200% 0}}


  .console-view{display:none}.console-view.active{display:block}
  .section-note{font-size:12px;color:var(--muted);margin:0 0 14px}
  .overview-grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:20px;margin-top:20px}
  .model-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
  .model-card .model-head{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--line)}
  .model-card h2{font-size:15px;margin:0}.model-card .model-head span{font-size:12px;color:var(--muted)}
  .model-row{display:grid;grid-template-columns:minmax(150px,1.6fr) .7fr .7fr .7fr;gap:10px;align-items:center;padding:13px 20px;border-bottom:1px solid var(--line)}
  .model-row:last-child{border-bottom:0}.model-row:hover{background:var(--panel-2)}
  .model-name{min-width:0}.model-name strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .model-name small{display:block;color:var(--muted);font-size:11px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .model-stat strong{display:block;font-size:13px}.model-stat small{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.4px}
  .health-list{padding:4px 20px}.health-row{display:flex;align-items:center;gap:9px;padding:13px 0;border-bottom:1px solid var(--line)}
  .health-row:last-child{border-bottom:0}.health-dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}
  .health-dot.good{background:var(--good);box-shadow:0 0 9px rgba(55,211,153,.7)}.health-dot.bad{background:var(--bad)}
  .health-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.health-meta{color:var(--muted);font-size:11px}
  .overview-empty{padding:28px 20px;text-align:center;color:var(--muted);font-size:13px}
  @media(max-width:900px){.overview-grid{grid-template-columns:1fr}}
  @media(max-width:720px){.model-row{grid-template-columns:minmax(130px,1fr) repeat(3,.65fr);padding:12px}.model-row .model-stat strong{font-size:12px}.model-row .model-stat small{font-size:9px}}
  @media(max-width:900px){
    .console-shell{grid-template-columns:180px minmax(0,1fr);gap:18px}
    .wrap{padding-left:18px;padding-right:18px}
  }
  @media(max-width:720px){
    .console-shell{display:block}
    .sidebar{position:static;margin-bottom:18px}
    .console-nav{display:grid;grid-template-columns:1fr 1fr}
    .sidebar-foot{display:none}
  }
  @media(max-width:720px){
    .stats{grid-template-columns:1fr}
    .form-grid{grid-template-columns:1fr}
    .prov .meta{display:none}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">R9</div>
    <div class="brand">
      <h1>Rev9 Apis</h1>
      <p>OpenAI-compatible endpoints · keys encrypted at rest</p>
    </div>
    <div class="spacer"></div>
    <button class="btn ghost" onclick="load()">Refresh</button>
    <form action="/logout" method="post" style="margin:0"><input type="hidden" name="csrf" value="__CSRF_TOKEN__"><button class="btn">Sign out</button></form>
  </header>


  <div class="console-shell">
    <aside class="sidebar">
      <div class="sidebar-title">Workspace</div>
      <nav class="console-nav" aria-label="Console sections">
        <button class="active" data-view="overview" onclick="switchView('overview',this)">Dashboard</button>
        <button data-view="keys" onclick="switchView('keys',this)">API Keys</button>
        <button data-view="logs" onclick="switchView('logs',this)">Usage Logs</button>
        <button data-view="usage" onclick="switchView('usage',this)">API Usage</button>
      </nav>
      <div class="sidebar-foot">Live gateway telemetry<br>Refresh to update metrics</div>
    </aside>
    <main class="console-main">
  <div class="console-view active" data-section="overview">
  <div class="stats">
    <div class="stat"><div class="label">Endpoints</div><div class="value" id="st-eps">—</div><div class="subtle">Configured providers</div></div>
    <div class="stat"><div class="label">Total requests</div><div class="value" id="st-req">—</div><div class="subtle">All gateway traffic</div></div>
    <div class="stat"><div class="label">Tokens metered</div><div class="value" id="st-tok">—</div><div class="subtle">Input + output tokens</div></div>
    <div class="stat"><div class="label">Healthy providers</div><div class="value" id="st-health">—</div><div class="subtle">Latest health checks</div></div>
  </div>
  <div class="overview-grid">
    <div class="model-card">
      <div class="model-head"><h2>Most used models</h2><span>Measured gateway activity</span></div>
      <div class="model-row" style="color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px">
        <span>Model</span><span>Requests</span><span>Tokens</span><span>Latency</span>
      </div>
      <div id="model-leaderboard"><div class="overview-empty">Loading model activity…</div></div>
    </div>
    <div class="model-card">
      <div class="model-head"><h2>Provider health</h2><span>Live checks</span></div>
      <div class="health-list" id="health-list"><div class="overview-empty">Loading provider health…</div></div>
    </div>
  </div>

  </div>
  <div class="console-view" data-section="keys">
  <div class="panel">
    <div class="head"><h2>API Keys</h2><span class="hint">Create and manage gateway keys</span></div>
    <div class="body"><p class="section-note">Each key routes requests to one configured upstream. The secret is shown only when created or rotated.</p><span class="hint">Upstream key is encrypted and never shown again</span></div>
    <div class="body">
      <div class="form-grid">
        <div class="field"><label>Name</label><input id="provider-name" placeholder="e.g. OpenAI Production"></div>
        <div class="field"><label>Auth prefix</label><input id="auth-prefix" value="Bearer" placeholder="Bearer"></div>
        <div class="field full"><label>Chat completions URL</label><input id="chat-url" placeholder="https://api.openai.com/v1/chat/completions"></div>
        <div class="field"><label>Models URL <span style="color:var(--muted-2)">(optional)</span></label><input id="models-url" placeholder="Auto-derived if left blank"></div>
        <div class="field"><label>Upstream API key</label><input id="upstream-key" type="password" placeholder="sk-..."></div>
      </div>
      <div class="form-actions"><button class="btn primary" onclick="createProvider()">Create endpoint</button></div>
    </div>
  </div>

  <div class="panel">
    <div class="head"><h2>Endpoints</h2><span class="hint" id="ep-count"></span></div>
    <div class="body"><div id="providers"><div class="skeleton"></div><div class="skeleton"></div></div></div>
  </div>

  <div class="panel">
    <div class="head"><h2>Playground</h2><span class="hint">Sends a real request through your gateway</span></div>
    <div class="body">
      <div class="form-grid">
        <div class="field"><label>Endpoint</label><select id="pg-provider" onchange="syncPlaygroundModels()"></select></div>
        <div class="field"><label>Model</label><select id="pg-model"></select></div>
        <div class="field full"><label>Gateway client key</label><input id="pg-key" type="password" autocomplete="off" placeholder="mgw_... (never saved)"></div>
        <div class="field full"><label>System prompt <span style="color:var(--muted-2)">(optional)</span></label><input id="pg-system" placeholder="You are a helpful assistant."></div>
        <div class="field full"><label>Message</label><textarea id="pg-message" rows="5" placeholder="Write a short greeting."></textarea></div>
      </div>
      <div class="form-actions"><button class="btn primary" id="pg-run" onclick="runPlayground()">Send test request</button></div>
      <pre class="response" id="pg-response">Choose an enabled endpoint, enter its client key, and send a request.</pre>
    </div>
  </div>

  <div class="panel">
    <div class="head"><h2>Media playground</h2><span class="hint">Image, speech, and video generation</span></div>
    <div class="body">
      <div class="form-grid">
        <div class="field"><label>Endpoint</label><select id="media-provider" onchange="loadMediaModels()"></select></div>
        <div class="field"><label>Model</label><select id="media-model"><option value="">Select a media model</option></select></div>
        <div class="field full"><label>Gateway client key</label><input id="media-key" type="password" placeholder="mgw_..."></div>
        <div class="field full"><label>Prompt or speech text</label><textarea id="media-prompt" rows="3" placeholder="Describe the image/video or enter text to speak"></textarea></div>
      </div>
      <div class="form-actions"><button class="btn primary" onclick="runMedia('image')">Generate image</button><button class="btn" onclick="runMedia('speech')">Generate speech</button><button class="btn" onclick="runMedia('video')">Generate video</button></div>
      <pre class="response" id="media-response">Choose a provider and media action.</pre>
    </div>
  </div>

  </div>
  <div class="console-view" data-section="logs">
  <div class="panel">
    <div class="head"><h2>Usage Logs</h2><span class="hint">Last 100 requests</span></div>
    <div class="body">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Provider</th><th>API</th><th>Model</th><th>Status</th><th>Tokens</th><th>Latency</th><th>Error</th></tr></thead>
          <tbody id="logs"><tr><td colspan="8" class="empty" style="padding:22px">No activity yet.</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
  </div>
  </div>
  <div class="console-view" data-section="usage">
    <div class="panel">
      <div class="head"><h2>API Usage</h2><span class="hint">Resource consumption and model trends</span></div>
      <div class="body">
        <div class="stats">
          <div class="stat"><div class="label">Requests</div><div class="value" id="usage-requests">—</div></div>
          <div class="stat"><div class="label">Tokens</div><div class="value" id="usage-tokens">—</div></div>
          <div class="stat"><div class="label">Average latency</div><div class="value" id="usage-latency">—</div></div>
        </div>
        <div class="table-wrap"><table><thead><tr><th>Provider</th><th>Model</th><th>Requests</th><th>Tokens</th><th>Avg latency</th><th>Errors</th></tr></thead><tbody id="usage-breakdown"></tbody></table></div>
      </div>
    </div>
  </div>
</div>

<div id="toasts"></div>
<div class="modal-bg" id="modal-bg">
  <div class="modal">
    <div class="head" id="modal-title">Confirm</div>
    <div class="body" id="modal-msg"></div>
    <div class="foot">
      <button class="btn ghost" onclick="closeModal(false)">Cancel</button>
      <button class="btn primary" id="modal-ok" onclick="closeModal(true)">Confirm</button>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>Number(n||0).toLocaleString();
let providers=[];

const CSRF='__CSRF_TOKEN__';
const api=(url,opts={})=>fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF,...(opts.headers||{})},...opts})
  .then(async r=>{let d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||('Request failed ('+r.status+')'));return d});

function toast(msg,kind='',title='',key=''){
  const t=document.createElement('div'); t.className='toast '+kind;
  t.innerHTML=(title?`<b>${esc(title)}</b>`:'')+esc(msg)+(key?`<div class="k">${esc(key)}</div>`:'');
  $('toasts').appendChild(t);
  setTimeout(()=>{t.style.transition='.3s';t.style.opacity='0';setTimeout(()=>t.remove(),300)}, key?9000:3600);
}

/* Promise-based confirm modal */
let modalResolve=null;
function confirmModal(title,msg,okLabel='Confirm',danger=false){
  $('modal-title').textContent=title; $('modal-msg').textContent=msg;
  const ok=$('modal-ok'); ok.textContent=okLabel; ok.className='btn '+(danger?'danger':'primary');
  $('modal-bg').classList.add('show');
  return new Promise(res=>modalResolve=res);
}
function closeModal(v){$('modal-bg').classList.remove('show'); if(modalResolve){modalResolve(v);modalResolve=null}}

async function createProvider(){
  const name=$('provider-name').value.trim(), chat=$('chat-url').value.trim();
  if(!name||!chat){toast('Name and chat URL are required.','bad');return}
  try{
    const p=await api('/api/providers',{method:'POST',body:JSON.stringify({
      name, chat_url:chat, models_url:$('models-url').value.trim(),
      upstream_api_key:$('upstream-key').value, auth_prefix:$('auth-prefix').value.trim()||'Bearer'
    })});
    toast('Copy it now — it will not be shown again.','good','Client key created',p.client_key);
    ['provider-name','chat-url','models-url','upstream-key'].forEach(i=>$(i).value='');
    load();
  }catch(e){toast(e.message,'bad','Could not create')}
}

function modelChecks(id,models,checked){return models.map(m=>`<label class="model-option"><input class="model-check" data-model="${esc(m)}" type="checkbox" ${checked.includes(m)?'checked':''}><span title="${esc(m)}">${esc(m)}</span></label>`).join('')}
function selectedModels(id){
  const checked=[...document.querySelectorAll('#s'+id+' .model-check:checked')].map(x=>x.dataset.model);
  const manual=$('m'+id).value.split(',').map(x=>x.trim()).filter(Boolean);
  return [...new Set([...checked,...manual])];
}

function syncPlaygroundModels(){
  const provider=providers.find(p=>String(p.id)===$('pg-provider').value);
  const models=provider?.allowed_models||[];
  $('pg-model').innerHTML=models.length ? models.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('') : '<option value="">No enabled models</option>';
}

function renderPlayground(){
  const select=$('pg-provider'), prior=select.value;
  const enabled=providers.filter(p=>p.enabled);
  select.innerHTML=enabled.length ? enabled.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('') : '<option value="">No enabled endpoints</option>';
  if(enabled.some(p=>String(p.id)===prior))select.value=prior;
  syncPlaygroundModels();
  $('media-provider').innerHTML=enabled.length?enabled.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join(''):'<option value="">No enabled endpoints</option>';
  loadMediaModels();
}

async function loadMediaModels(){
  const id=$('media-provider').value;
  if(!id)return;
  $('media-model').innerHTML='<option value="">Loading models…</option>';
  try{const x=await api('/api/providers/'+id+'/discover-media-models',{method:'POST'});$('media-model').innerHTML=(x.models||[]).map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('')||'<option value="">No media models found</option>'}
  catch(e){$('media-model').innerHTML='<option value="">Discovery failed</option>';toast(e.message,'bad','Media model discovery failed')}
}

const VIDEO_POLL_INTERVAL_MS=5000, VIDEO_POLL_MAX_ATTEMPTS=60;
let mediaRun=0;

async function pollVideo(publicId,key,operationId,output,run){
  const started=Date.now();
  for(let attempt=1;attempt<=VIDEO_POLL_MAX_ATTEMPTS;attempt++){
    await new Promise(r=>setTimeout(r,VIDEO_POLL_INTERVAL_MS));
    if(run!==mediaRun)return;
    const elapsed=Math.round((Date.now()-started)/1000);
    output.textContent=`Generating video… ${elapsed}s elapsed (poll ${attempt}/${VIDEO_POLL_MAX_ATTEMPTS}).`;
    let body;
    try{
      const r=await fetch(`/v1/videos/${encodeURIComponent(operationId)}`,{headers:{'Authorization':'Bearer '+key},signal:AbortSignal.timeout(20000)});
      const raw=await r.text();
      if(run!==mediaRun)return;
      try{body=JSON.parse(raw)}catch{body={status:'processing',error:raw}}
      if(r.status===404){output.textContent='Video operation expired or was removed.';toast('Video operation expired.','bad','Media');return}
      if(!r.ok){output.textContent=`Status check failed (HTTP ${r.status}). Retrying…`;continue}
    }catch(e){output.textContent=`Status check failed: ${e.message}. Retrying…`;continue}
    if(body.status==='completed'&&body.output){
      output.textContent='';
      const a=document.createElement('video');a.controls=true;a.src=body.output;a.style.maxWidth='100%';a.style.borderRadius='9px';
      const d=document.createElement('div');d.style.marginTop='8px';
      const l=document.createElement('a');l.href=body.output;l.download='';l.textContent='Download video';
      d.appendChild(l);output.appendChild(a);output.appendChild(d);
      toast('Video ready.','good','Media');return;
    }
    if(body.status==='failed'){output.textContent='Video generation failed.'+(body.error?' '+body.error:'');toast('Video generation failed.','bad','Media');return}
  }
  output.textContent='Stopped waiting for video. The operation may still finish. Status URL: '+`/v1/videos/${encodeURIComponent(operationId)}`;
  toast('Video polling timed out.','bad','Media');
}

async function runMedia(kind){
  const p=providers.find(x=>String(x.id)===$('media-provider').value),key=$('media-key').value.trim(),model=$('media-model').value.trim(),prompt=$('media-prompt').value.trim();
  if(!p||!key||!model||!prompt){toast('Provider, key, model, and prompt are required.','bad','Media');return}
  const run=++mediaRun;
  const path=kind==='image'?`/v1/images/generations`:kind==='video'?`/v1/videos/generations`:`/v1/audio/speech`;
  const payload=kind==='speech'?{model,input:prompt,voice:'alloy',response_format:'mp3'}:{model,prompt};
  const output=$('media-response');
  output.textContent=kind==='video'?'Submitting video generation…':'Sending…';
  try{
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body:JSON.stringify(payload)});
    const raw=await r.text();
    if(run!==mediaRun)return;
    await load(true);
    if(kind==='image'&&r.ok){
      const body=JSON.parse(raw);output.textContent='';
      for(const item of body.data||[]){
        const img=document.createElement('img');img.src=item.url;img.alt='Generated image';img.style.maxWidth='100%';
        const link=document.createElement('a');link.href=item.url;link.download='';link.textContent='Download image';
        const row=document.createElement('div');row.appendChild(img);row.appendChild(link);output.appendChild(row);
      }
      return;
    }
    if(kind==='video'&&r.ok){
      let body={};
      try{body=JSON.parse(raw)}catch{}
      const operationId=body.id;
      if(body.status==='failed'){output.textContent='Video generation failed.'+(body.error?' '+body.error:'');toast('Video generation failed.','bad','Media');return}
      if(operationId&&body.poll_url){await pollVideo(p.public_id,key,operationId,output,run);return}
      if(body.output){
        output.textContent='';
        const a=document.createElement('video');a.controls=true;a.src=body.output;a.style.maxWidth='100%';a.style.borderRadius='9px';
        const d=document.createElement('div');d.style.marginTop='8px';
        const l=document.createElement('a');l.href=body.output;l.download='';l.textContent='Download video';
        d.appendChild(l);output.appendChild(a);output.appendChild(d);return;
      }
    }
    let body=raw;
    try{body=JSON.stringify(JSON.parse(raw),null,2)}catch{}
    output.textContent=`HTTP ${r.status}\n\n${body}`;
    if(!r.ok)toast('Media request failed.','bad','Media');
  }catch(e){output.textContent=e.message;toast(e.message,'bad','Media')}}

function switchView(view, button){
  document.querySelectorAll('.console-view').forEach(el => el.classList.toggle('active', el.dataset.section === view));
  document.querySelectorAll('.console-nav button').forEach(el => el.classList.toggle('active', el === button));
}
function renderUsageSummary(u){
  const breakdown = u.breakdown || [];
  const requests = Number(u.requests || 0);
  const tokens = Number(u.tokens || 0);
  const latency = breakdown.length ? Math.round(breakdown.reduce((sum, row) => sum + Number(row.avg_latency_ms || 0), 0) / breakdown.length) : 0;
  $('usage-requests').textContent = fmt(requests);
  $('usage-tokens').textContent = fmt(tokens);
  $('usage-latency').textContent = latency ? fmt(latency) + ' ms' : '—';
  $('usage-breakdown').innerHTML = breakdown.length ? breakdown.map(row => `<tr><td>${esc(row.provider_id || '—')}</td><td>${esc(row.model || '—')}</td><td>${fmt(row.requests)}</td><td>${fmt(row.tokens)}</td><td>${fmt(row.avg_latency_ms)} ms</td><td>${fmt(row.errors)}</td></tr>`).join('') : '<tr><td colspan="6" class="empty">No usage data yet.</td></tr>';
}
function renderModelLeaderboard(models){
  const list = (models || []).slice().sort((a,b) => Number(b.observed?.requests||0) - Number(a.observed?.requests||0));
  $('model-leaderboard').innerHTML = list.length ? list.slice(0,8).map(model => {
    const observed = model.observed || {};
    const requests = Number(observed.requests || 0);
    const tokens = Number(observed.total_tokens || 0);
    const latency = observed.avg_latency_ms == null ? '—' : fmt(observed.avg_latency_ms) + ' ms';
    return `<div class="model-row">
      <div class="model-name"><strong title="${esc(model.id)}">${esc(model.id)}</strong><small>${esc(model.provider)}${requests ? '' : ' · No traffic yet'}</small></div>
      <div class="model-stat"><strong>${fmt(requests)}</strong><small>requests</small></div>
      <div class="model-stat"><strong>${requests ? fmt(tokens) : '—'}</strong><small>${requests ? 'tokens' : 'tokens'}</small></div>
      <div class="model-stat"><strong>${latency}</strong><small>avg latency</small></div>
    </div>`;
  }).join('') : '<div class="overview-empty">No enabled models configured yet. Add an API key and discover models to populate this view.</div>';
}
function renderProviderHealth(data){
  const rows = data?.providers || [];
  const healthy = rows.filter(row => row.status === 'healthy').length;
  $('st-health').textContent = rows.length ? `${healthy}/${rows.length}` : '0';
  $('health-list').innerHTML = rows.length ? rows.map(row => {
    const good = row.status === 'healthy';
    const status = row.status || 'unchecked';
    const latency = row.latency_ms ? ` · ${fmt(row.latency_ms)} ms` : '';
    return `<div class="health-row"><span class="health-dot ${good ? 'good' : (row.status === 'unhealthy' ? 'bad' : '')}"></span><span class="health-name" title="${esc(row.name)}">${esc(row.name)}</span><span class="health-meta">${esc(status)}${latency}</span></div>`;
  }).join('') : '<div class="overview-empty">No providers configured.</div>';
}
async function load(activityOnly=false){
  let ps=[],u={requests:0,tokens:0,logs:[],breakdown:[]},models={models:[]},health={providers:[]};
  try{[ps,u,models,health]=await Promise.all([api('/api/providers'),api('/api/usage'),fetch('/models/public',{credentials:'same-origin'}).then(r=>r.json()),api('/health/providers')]);}
  catch(e){toast(e.message,'bad','Load failed');return}

  $('st-eps').textContent=fmt(ps.length);
  $('st-req').textContent=fmt(u.requests);
  $('st-tok').textContent=fmt(u.tokens);
  $('ep-count').textContent=ps.length?`${ps.length} configured`:'';
  providers=ps;
  renderModelLeaderboard(models.models);
  renderProviderHealth(health);
  renderUsageSummary(u);
  if(!activityOnly)renderPlayground();

  const openIds=new Set([...document.querySelectorAll('.prov.open')].map(e=>e.dataset.id));
  const base=location.origin;
  $('providers').innerHTML = ps.length ? ps.map(p=>{
    const on=p.enabled;
    return `<div class="prov ${openIds.has(String(p.id))?'open':''}" data-id="${p.id}">
      <div class="top" onclick="toggle(this)">
        <span class="chev">▶</span>
        <span class="name">${esc(p.name)}</span>
        <span class="badge ${on?'on':'off'}"><span class="dot"></span>${on?'Enabled':'Disabled'}</span>
        <span class="meta">${p.allowed_models.length} model${p.allowed_models.length===1?'':'s'} · added ${esc((p.created_at||'').slice(0,10))}</span>
      </div>
      <div class="detail">
        <div class="row">
          <div class="url-box endpoint-urls"><div><b>OpenAI</b><br><code>${esc(base)}/v1</code></div><div><b>Codex (Responses · Bearer)</b><br><code>${esc(base)}/v1/responses</code></div><div><b>Anthropic (x-api-key + anthropic-version)</b><br><code>${esc(base)}/v1/messages</code></div><div><b>API key</b><br><code>mgw_…</code></div></div>
          <button class="btn sm ghost" onclick="copy('${esc(base)}/v1',event)">Copy OpenAI</button>
        </div>
        <div class="subtle" id="usage-${p.id}">Loading usage…</div>
        <div class="subtle">Allowed models — tick to enable; untick to disable</div>
        <div class="model-list" id="s${p.id}">${modelChecks(p.id,p.allowed_models,p.allowed_models) || '<span class="subtle" style="margin:0">Fetch models or add IDs below.</span>'}</div>
        <div class="subtle">Add model IDs manually, comma-separated</div>
        <input id="m${p.id}" placeholder="gpt-4o, gpt-4o-mini">
        <div class="actions">
          <button class="btn sm ghost" onclick="discover(${p.id})">Fetch from provider</button>
          <button class="btn sm ghost" onclick="healthCheck(${p.id})">Check health</button>
          <button class="btn sm primary" onclick="save(${p.id})">Save models</button>
          <button class="btn sm ghost" onclick="toggleEnabled(${p.id},${on?0:1})">${on?'Disable':'Enable'}</button>
          <button class="btn sm danger" onclick="rotate(${p.id})">Rotate key</button>
          <button class="btn sm danger" onclick="removeProvider(${p.id},'${esc(p.name)}')">Delete endpoint</button>
          <span id="d${p.id}" class="subtle" style="margin:0 0 0 4px"></span><span id="h${p.id}" class="subtle" style="margin:0 0 0 4px"></span>
        </div>
      </div>
    </div>`;
  }).join('') : '<div class="empty">No endpoints yet — create one above to get started.</div>';

  const logs=u.logs||[];
  $('logs').innerHTML = logs.length ? logs.map(x=>{
    const okStatus=x.status>=200&&x.status<400;
    return `<tr>
      <td class="mono">${esc((x.created_at||'').replace('T',' ').replace('+00:00',''))}</td>
      <td>${esc(x.provider_name||'(removed)')}</td>
      <td><span class="pill">${esc(x.api_name||'chat')}</span></td>
      <td>${esc(x.model||'—')}</td>
      <td><span class="pill ${okStatus?'ok':'err'}">${esc(x.status)}</span></td>
      <td>${fmt(x.total_tokens)}</td>
      <td class="mono">${fmt(x.latency_ms)} ms</td>
      <td class="err-cell" title="${esc(x.error||'')}">${esc(x.error||'')}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="8" class="empty" style="padding:22px">No activity yet.</td></tr>';
  api('/health/providers').then(x=>{const rows=x.providers||[];$('st-health').textContent=rows.filter(p=>p.status==='healthy').length+'/'+rows.length;rows.forEach(p=>{const el=$('h'+p.id);if(el)el.textContent=(p.status||'unknown')+' · '+(p.latency_ms||0)+' ms'})}).catch(()=>{$('st-health').textContent='—'});
  (u.breakdown||[]).forEach(x=>{const el=$('usage-'+x.provider_id);if(el)el.textContent=`${fmt(x.requests)} requests · ${fmt(x.tokens)} tokens · ${fmt(x.avg_latency_ms)} ms avg · ${fmt(x.errors)} errors`});
}

function toggle(el){el.parentElement.classList.toggle('open')}

async function copy(text,ev){
  try{await navigator.clipboard.writeText(text); toast('Copied to clipboard.','good');}
  catch{ toast('Copy failed — select manually.','bad'); }
  if(ev)ev.stopPropagation();
}

async function save(id){
  const allowed_models=selectedModels(id);
  try{await api('/api/providers/'+id,{method:'PATCH',body:JSON.stringify({allowed_models})}); toast('Models saved.','good'); load();}
  catch(e){toast(e.message,'bad','Save failed')}
}

async function toggleEnabled(id,enabled){
  try{
    await api('/api/providers/'+id,{method:'PATCH',body:JSON.stringify({allowed_models:selectedModels(id),enabled})});
    toast(enabled?'Endpoint enabled.':'Endpoint disabled.','good'); load();
  }catch(e){toast(e.message,'bad')}
}

async function discover(id){
  $('d'+id).textContent='Fetching…';
  try{
    const x=await api('/api/providers/'+id+'/discover-models',{method:'POST'});
    const manual=$('m'+id).value.split(',').map(s=>s.trim()).filter(s=>s&&!x.models.includes(s));
    $('m'+id).value=manual.join(', ');
    $('s'+id).innerHTML=modelChecks(id,x.models,x.models) || '<span class="subtle" style="margin:0">No models returned by provider.</span>';
    $('d'+id).textContent=`Found ${x.models.length} models — all ticked; untick any you do not want to enable, then Save.`;
  }catch(e){$('d'+id).textContent=''; toast(e.message,'bad','Discovery failed')}
}

async function healthCheck(id){
  const el=$('h'+id); if(el)el.textContent='Checking…';
  try{const x=await api('/api/providers/'+id+'/health',{method:'POST'});if(el)el.textContent=x.status+' · '+x.latency_ms+' ms';toast('Provider health updated.','good')}
  catch(e){if(el)el.textContent='unhealthy';toast(e.message,'bad','Health check failed')}
}

async function removeProvider(id,name){
  if(!await confirmModal('Delete endpoint',`Delete "${name}" and its request history? This cannot be undone.`,'Delete endpoint',true))return;
  try{await api('/api/providers/'+id,{method:'DELETE'});toast('Endpoint and local telemetry deleted.','good');load()}
  catch(e){toast(e.message,'bad','Delete failed')}
}

async function runPlayground(){
  const provider=providers.find(p=>String(p.id)===$('pg-provider').value);
  const model=$('pg-model').value, key=$('pg-key').value.trim(), message=$('pg-message').value.trim(), system=$('pg-system').value.trim();
  const missing=[];
  if(!provider)missing.push('endpoint');
  if(!model)missing.push('model');
  if(!key)missing.push('client key');
  if(!message)missing.push('message');
  if(missing.length){toast(`Missing: ${missing.join(', ')}.`, 'bad', 'Playground');return}
  const button=$('pg-run'), output=$('pg-response');
  button.disabled=true; button.textContent='Sending…'; output.textContent='Sending request…';
  const messages=[...(system?[{role:'system',content:system}]:[]),{role:'user',content:message}];
  const started=performance.now();
  try{
    const response=await fetch(`/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body:JSON.stringify({model,messages})});
    const raw=await response.text();
    let body=raw;
    try{body=JSON.stringify(JSON.parse(raw),null,2)}catch{}
    output.textContent=`HTTP ${response.status} · ${Math.round(performance.now()-started)} ms\n\n${body}`;
    if(!response.ok)toast(`Gateway returned HTTP ${response.status}.`,'bad','Playground');
    else toast('Request completed.','good','Playground');
  }catch(error){output.textContent=`Request failed · ${Math.round(performance.now()-started)} ms\n\n${error.message}`;toast(error.message,'bad','Playground')}
  finally{button.disabled=false;button.textContent='Send test request'}
}

async function rotate(id){
  if(!await confirmModal('Rotate client key','Existing clients using the current key will immediately stop working. A new key is shown once.','Rotate key',true))return;
  try{const x=await api('/api/providers/'+id+'/rotate-key',{method:'POST'});
    toast('Copy it now — shown only once.','good','New client key',x.client_key);}
  catch(e){toast(e.message,'bad','Rotation failed')}
}

document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal(false)});
load();
</script>
</body>
</html>'''


SPA_ROUTES = {"/", "/requests", "/docs"}

@app.get("/{spa_path:path}", include_in_schema=False)
async def spa_fallback(spa_path: str, request: Request) -> Response:
    """Serve the landing page only for known SPA routes, never for API endpoints."""
    full = "/" + spa_path if spa_path else "/"
    if full not in SPA_ROUTES:
        raise HTTPException(status_code=404, detail="Not found")
    return _frontend_index()

