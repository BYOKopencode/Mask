import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    for name, value in {
        "DASHBOARD_PASSWORD": "test-only-password",
        "FERNET_KEY": Fernet.generate_key().decode(),
        "SESSION_SECRET": "test-only-session-secret",
        "GATEWAY_DB": str(tmp_path / "test.db"),
        "COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("gateway_under_test", Path(__file__).with_name("mask.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        login_page = client.get("/rev9").text
        login_csrf = login_page.split('name="csrf" value="')[1].split('"')[0]
        client.post("/rev9", data={"password": "test-only-password", "csrf": login_csrf})
        page = client.get("/rev9").text
        csrf = page.split("const CSRF='")[1].split("'")[0]
        headers = {"x-csrf-token": csrf}
        provider = client.post("/api/providers", headers=headers, json={
            "name": "Mock", "chat_url": "https://upstream.example/v1",
            "upstream_api_key": "upstream-secret",
        }).json()
        client.patch(f"/api/providers/{provider['id']}", headers=headers,
                     json={"allowed_models": ["test-model"]})
        yield module, client, provider["client_key"]


@pytest.mark.parametrize("protocol", ["messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_native_contract(gateway, protocol, stream):
    module, client, key = gateway
    anthropic = protocol == "messages"
    payload = {"model": "test-model", "stream": stream}
    if anthropic:
        payload.update(max_tokens=100, system="Be concise", messages=[
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "42"}]}],
            tools=[{"name": "lookup", "input_schema": {"type": "object"}}])
    else:
        payload.update(instructions="Be concise", input=[
            {"type": "function_call_output", "call_id": "call_1", "output": "42"}],
            tools=[{"type": "function", "name": "lookup", "parameters": {"type": "object"}}])
    event = "content_block_delta" if anthropic else "response.output_text.delta"
    body = f'event: {event}\ndata: {{"type":"{event}","delta":"hello"}}\n\n'.encode()
    result = {"id": "native-result", "usage": {"input_tokens": 3, "output_tokens": 2}}

    def upstream(request):
        assert str(request.url) == f"https://upstream.example/v1/{protocol}"
        assert json.loads(request.content) == payload
        assert key not in str(request.headers)
        if anthropic:
            assert request.headers["x-api-key"] == "upstream-secret"
            assert request.headers["anthropic-version"] == "2023-06-01"
        else:
            assert request.headers["authorization"] == "Bearer upstream-secret"
        return httpx.Response(200, content=body if stream else json.dumps(result).encode(),
                              headers={"content-type": "text/event-stream" if stream else "application/json"})

    original = module.app.state.http
    module.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"} if anthropic else {"authorization": f"Bearer {key}"}
    response = client.post(f"/v1/{protocol}", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == body if stream else response.json() == result
    assert len(module._rate_buckets[next(k for k in module._rate_buckets if k.startswith("key:"))]) == 1
    if not stream:
        usage = client.get("/api/usage").json()
        assert usage["tokens"] == 5
    asyncio.run(module.app.state.http.aclose())
    module.app.state.http = original


def test_validation_and_auth(gateway):
    _, client, key = gateway
    base = {"model": "test-model", "messages": [{"role": "user", "content": "Hi"}]}
    assert client.post("/v1/messages", json=base).status_code == 401
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    assert client.post("/v1/messages", json=base, headers=headers).status_code == 400
    assert client.post("/v1/messages", json={**base, "max_tokens": True}, headers=headers).status_code == 400
    assert client.post("/v1/messages", json={**base, "max_tokens": 5}, headers={"x-api-key": key}).status_code == 400

@pytest.mark.parametrize("protocol", ["messages", "responses"])
def test_upstream_error_is_not_success(gateway, protocol):
    module, client, key = gateway
    original = module.app.state.http
    module.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(500, json={"secret": "private-upstream-details"})))
    payload = {"model": "test-model", "input": "Hi", "max_tokens": 10,
               "messages": [{"role": "user", "content": "Hi"}]}
    response = client.post("/v1/" + protocol, json=payload, headers={
        "authorization": "Bearer " + key, "anthropic-version": "2023-06-01"})
    assert response.status_code == 502
    assert "private-upstream-details" not in response.text
    assert "error" in response.json()
    assert module.app.state.upstream_slots._value == module.MAX_CONCURRENT_REQUESTS
    asyncio.run(module.app.state.http.aclose())
    module.app.state.http = original
