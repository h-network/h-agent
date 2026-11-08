#!/usr/bin/env python3
"""Deployment-neutral Claude/Codex SDK runner with NDJSON and SSE outputs.

This module deliberately owns no container, process-supervisor, TLS, or reverse
proxy behavior.  It can run directly on a host or inside an environment chosen
by the operator.  Provider SDKs are imported lazily so ``--help`` and the health
endpoint remain useful when a provider dependency is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hmac
import json
import os
import pathlib
import sys
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
PROVIDERS = frozenset({"claude", "codex"})
MAX_REQUEST_BYTES = 1_048_576


class ConfigurationError(RuntimeError):
    """An operator-supplied runtime setting is unusable."""


class RunCancelled(RuntimeError):
    """A run observed a cancellation request between provider events."""


def _json_safe(value: Any) -> Any:
    """Convert SDK models into JSON-compatible values without using repr()."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(by_alias=True, exclude_none=True))
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    return str(value)


def _token_env_name(name: str) -> str:
    return "HAGENT_MCP_TOKEN_" + name.replace("-", "_")


def _default_mcp_file() -> pathlib.Path:
    configured = os.environ.get("AGENT_MCP_FILE")
    if configured:
        return pathlib.Path(configured).expanduser()
    return pathlib.Path.home() / ".config" / "h-agent" / "mcp.json"


def load_http_mcp_servers(path: pathlib.Path | None = None) -> dict[str, dict[str, str]]:
    """Load HTTP MCP entries from h-agent's existing registry.

    Tokens are resolved from their file/env reference at run time and retained
    only in memory.  The returned mapping is never included in API responses or
    event payloads.  Stdio MCP entries are intentionally ignored by the web
    service: a network-facing chat must not gain a local process launcher merely
    because the interactive CLI registry contains one.
    """
    registry_path = path or _default_mcp_file()
    if not registry_path.exists():
        return {}
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read MCP registry {registry_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"MCP registry {registry_path} must be a JSON object")

    servers: dict[str, dict[str, str]] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for char in name
        ):
            raise ConfigurationError(f"invalid MCP server name in {registry_path}: {name!r}")
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        url = entry["url"]
        if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
            raise ConfigurationError(f"MCP server {name!r} has an invalid HTTP URL")

        token = ""
        token_file = entry.get("token_file")
        token_env = entry.get("token_env")
        if token_file:
            if not isinstance(token_file, str):
                raise ConfigurationError(f"MCP server {name!r} has an invalid token_file")
            try:
                token = pathlib.Path(token_file).expanduser().read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigurationError(
                    f"MCP server {name!r} token file is unreadable: {token_file}"
                ) from exc
        elif token_env:
            if not isinstance(token_env, str):
                raise ConfigurationError(f"MCP server {name!r} has an invalid token_env")
            token = os.environ.get(token_env, "").strip()
        if (token_file or token_env) and not token:
            raise ConfigurationError(f"MCP server {name!r} bearer token is empty")

        server = {"url": url}
        if token:
            server["token"] = token
        servers[name] = server
    return servers


@dataclasses.dataclass
class RunRecord:
    id: str
    provider: str
    prompt: str
    cwd: str
    options: dict[str, Any]
    created_at: float = dataclasses.field(default_factory=time.time)
    status: str = "queued"
    result: str = ""
    error: str = ""
    cancel_requested: bool = False
    events: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    condition: threading.Condition = dataclasses.field(default_factory=threading.Condition)

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        with self.condition:
            event = {
                "sequence": len(self.events) + 1,
                "type": event_type,
                "run_id": self.id,
                "provider": self.provider,
                "timestamp": time.time(),
                **{key: _json_safe(value) for key, value in payload.items() if value is not None},
            }
            self.events.append(event)
            self.condition.notify_all()
            return event

    def events_after(self, sequence: int) -> list[dict[str, Any]]:
        with self.condition:
            return [event.copy() for event in self.events if event["sequence"] > sequence]

    def wait_after(self, sequence: int, timeout: float) -> list[dict[str, Any]]:
        with self.condition:
            available = [event.copy() for event in self.events if event["sequence"] > sequence]
            if available or self.status in TERMINAL_STATES:
                return available
            self.condition.wait(timeout)
            return [event.copy() for event in self.events if event["sequence"] > sequence]

    def public(self) -> dict[str, Any]:
        with self.condition:
            return {
                "id": self.id,
                "provider": self.provider,
                "cwd": self.cwd,
                "created_at": self.created_at,
                "status": self.status,
                "result": self.result if self.status == "completed" else None,
                "error": self.error if self.status == "failed" else None,
                "last_sequence": len(self.events),
            }


ProviderRunner = Callable[[RunRecord, dict[str, dict[str, str]]], Any]


class RunManager:
    def __init__(
        self,
        *,
        mcp_servers: dict[str, dict[str, str]] | None = None,
        runners: dict[str, ProviderRunner] | None = None,
    ) -> None:
        self.mcp_servers = mcp_servers or {}
        self.runners = runners or {"claude": run_claude, "codex": run_codex}
        self.runs: dict[str, RunRecord] = {}
        self.lock = threading.Lock()

    def start(
        self,
        *,
        provider: str,
        prompt: str,
        cwd: str,
        options: dict[str, Any] | None = None,
    ) -> RunRecord:
        if provider not in PROVIDERS:
            raise ValueError(f"unsupported provider: {provider}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must not be empty")
        workdir = pathlib.Path(cwd).expanduser().resolve()
        if not workdir.is_dir():
            raise ValueError(f"workspace is not a directory: {cwd}")
        record = RunRecord(
            id=uuid.uuid4().hex,
            provider=provider,
            prompt=prompt,
            cwd=str(workdir),
            options=options or {},
        )
        with self.lock:
            self.runs[record.id] = record
        thread = threading.Thread(target=self._execute, args=(record,), daemon=True)
        thread.start()
        return record

    def get(self, run_id: str) -> RunRecord | None:
        with self.lock:
            return self.runs.get(run_id)

    def cancel(self, run_id: str) -> RunRecord | None:
        record = self.get(run_id)
        if record is None:
            return None
        with record.condition:
            if record.status not in TERMINAL_STATES:
                record.cancel_requested = True
                record.condition.notify_all()
        return record

    def _execute(self, record: RunRecord) -> None:
        record.status = "running"
        record.emit("run.started", cwd=record.cwd)
        try:
            result = asyncio.run(self.runners[record.provider](record, self.mcp_servers))
            if record.cancel_requested:
                raise RunCancelled("cancel requested")
            record.result = result or ""
            record.status = "completed"
            record.emit("run.completed", result=record.result)
        except RunCancelled:
            record.status = "cancelled"
            record.emit("run.cancelled")
        except Exception as exc:  # provider errors become terminal stream events
            record.error = f"{type(exc).__name__}: {exc}"
            record.status = "failed"
            record.emit("run.failed", error=record.error)


async def run_claude(
    record: RunRecord, mcp_servers: dict[str, dict[str, str]]
) -> str:
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            query,
        )
    except ImportError as exc:
        raise ConfigurationError(
            "Claude SDK support requires claude-agent-sdk (see requirements-sdk-web.txt)"
        ) from exc

    claude_mcp: dict[str, dict[str, Any]] = {}
    for name, server in mcp_servers.items():
        config: dict[str, Any] = {"type": "http", "url": server["url"]}
        if server.get("token"):
            config["headers"] = {"Authorization": f"Bearer {server['token']}"}
        claude_mcp[name] = config

    option_values: dict[str, Any] = {
        "cwd": record.cwd,
        "permission_mode": "bypassPermissions",
        # The web service does not expose the host's built-in file/shell tools.
        # Network tools arrive only through explicitly registered HTTP MCP servers.
        "tools": [],
        "mcp_servers": claude_mcp,
        "include_partial_messages": True,
        "env": {**os.environ, "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1"},
    }
    for key in ("model", "max_turns", "max_budget_usd", "system_prompt"):
        value = record.options.get(key)
        if value is not None:
            option_values[key] = value
    options = ClaudeAgentOptions(**option_values)
    result_text = ""
    saw_text_delta = False

    async for message in query(prompt=record.prompt, options=options):
        if record.cancel_requested:
            raise RunCancelled("cancel requested")
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                record.emit("provider.started", subtype=message.subtype)
            else:
                record.emit("provider.event", event_class="SystemMessage", subtype=message.subtype)
            continue
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text and not saw_text_delta:
                    record.emit("assistant.message", text=block.text)
                elif isinstance(block, ToolUseBlock):
                    record.emit("tool.started", tool=block.name, tool_use_id=block.id)
                elif isinstance(block, ToolResultBlock):
                    record.emit(
                        "tool.completed",
                        tool_use_id=block.tool_use_id,
                        is_error=block.is_error,
                    )
            if message.usage:
                record.emit("usage.updated", usage=message.usage)
            continue
        if isinstance(message, StreamEvent):
            event = message.event if isinstance(message.event, dict) else {}
            delta = event.get("delta")
            if (
                event.get("type") == "content_block_delta"
                and isinstance(delta, dict)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("text"), str)
            ):
                saw_text_delta = True
                record.emit("assistant.delta", text=delta["text"])
            else:
                record.emit(
                    "provider.event",
                    event_class="StreamEvent",
                    source_event_type=event.get("type"),
                )
            continue
        if isinstance(message, ResultMessage):
            result_text = message.result or result_text
            record.emit(
                "provider.completed",
                is_error=message.is_error,
                turns=message.num_turns,
                duration_ms=message.duration_ms,
                cost_usd=message.total_cost_usd,
                usage=message.usage,
                session_id=message.session_id,
            )
            continue
        record.emit("provider.event", event_class=type(message).__name__)
    return result_text


def _toml_string(value: str) -> str:
    # JSON double-quoted strings are valid TOML basic strings for these values.
    return json.dumps(value, ensure_ascii=False)


async def run_codex(
    record: RunRecord, mcp_servers: dict[str, dict[str, str]]
) -> str:
    try:
        from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
        from openai_codex._run import (
            ItemCompletedNotification,
            ThreadTokenUsageUpdatedNotification,
            TurnCompletedNotification,
        )
        from openai_codex.generated.v2_all import (
            AgentMessageDeltaNotification,
            AgentMessageThreadItem,
            ItemStartedNotification,
        )
    except ImportError as exc:
        raise ConfigurationError(
            "Codex SDK support requires openai-codex (see requirements-sdk-web.txt)"
        ) from exc

    child_env = dict(os.environ)
    overrides = [
        "features.shell_tool=false",
        "features.unified_exec=false",
    ]
    for name, server in mcp_servers.items():
        parts = [
            f"url={_toml_string(server['url'])}",
            'default_tools_approval_mode="approve"',
        ]
        if server.get("token"):
            env_name = _token_env_name(name)
            child_env[env_name] = server["token"]
            parts.append(f"bearer_token_env_var={_toml_string(env_name)}")
        overrides.append(f"mcp_servers.{name}=" + "{" + ",".join(parts) + "}")

    config = CodexConfig(
        cwd=record.cwd,
        env=child_env,
        config_overrides=tuple(overrides),
    )
    result_text = ""
    saw_text_delta = False
    async with AsyncCodex(config) as codex:
        thread = await codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            cwd=record.cwd,
            ephemeral=True,
            model=record.options.get("model"),
            sandbox=Sandbox.read_only,
            developer_instructions=(
                "Use only the configured remote MCP tools. Local shell and filesystem "
                "execution are disabled for this web service."
            ),
        )
        record.emit("provider.started")
        handle = await thread.turn(record.prompt)
        async for event in handle.stream():
            if record.cancel_requested:
                raise RunCancelled("cancel requested")
            payload = event.payload
            if isinstance(payload, AgentMessageDeltaNotification):
                saw_text_delta = True
                if payload.delta:
                    record.emit("assistant.delta", text=payload.delta)
                continue
            if isinstance(payload, ItemStartedNotification):
                item = payload.item.root
                item_type = str(getattr(item, "type", type(item).__name__))
                if "tool" in item_type.lower() or "command" in item_type.lower():
                    record.emit("tool.started", tool=item_type)
                else:
                    record.emit("provider.item.started", item_type=item_type)
                continue
            if isinstance(payload, ItemCompletedNotification):
                item = payload.item.root
                if isinstance(item, AgentMessageThreadItem):
                    result_text = item.text or result_text
                    if item.text and not saw_text_delta:
                        record.emit("assistant.message", text=item.text)
                else:
                    item_type = str(getattr(item, "type", type(item).__name__))
                    status = getattr(item, "status", None)
                    if "tool" in item_type.lower() or "command" in item_type.lower():
                        record.emit("tool.completed", tool=item_type, status=status)
                    else:
                        record.emit("provider.item.completed", item_type=item_type, status=status)
                continue
            if isinstance(payload, ThreadTokenUsageUpdatedNotification):
                record.emit("usage.updated", usage=getattr(payload, "token_usage", None))
                continue
            if isinstance(payload, TurnCompletedNotification):
                turn = payload.turn
                status = getattr(turn.status, "value", str(turn.status))
                record.emit(
                    "provider.completed",
                    status=status,
                    duration_ms=getattr(turn, "duration_ms", None),
                )
                continue
            record.emit("provider.event", event_class=type(payload).__name__)
    return result_text


_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>h-agent SDK</title>
<style>
:root{color-scheme:dark;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
body{margin:0;background:#0c1117;color:#dbe7f3}main{max-width:920px;margin:auto;padding:1.2rem}
.bar{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}select,input,textarea,button{font:inherit}
textarea{width:100%;box-sizing:border-box;min-height:8rem;background:#101923;color:#eef;border:1px solid #345;padding:.8rem}
button{padding:.55rem .9rem;background:#1b6f8a;color:white;border:0;border-radius:.25rem;cursor:pointer}
#events{margin-top:1rem;white-space:pre-wrap}.event{border-top:1px solid #263747;padding:.55rem 0}
.kind{color:#66d9ef}.bad{color:#ff7b72}.muted{color:#8796a5}.answer{color:#d2f5c4}
</style></head><body><main>
<h2>h-agent SDK</h2>
<div class="bar"><label>Provider <select id="provider"><option>claude</option><option>codex</option></select></label>
<label>Bearer token <input id="token" type="password" autocomplete="off"></label>
<span id="status" class="muted">idle</span></div>
<p><textarea id="prompt" placeholder="Ask the agent…"></textarea></p>
<div class="bar"><button id="run">Run</button><button id="cancel" disabled>Cancel</button></div>
<div id="events"></div>
<script>
const $=id=>document.getElementById(id),apiBase=location.pathname.replace(/\/ui\/?$/,'/api'); let current=null,answerEl=null;
function headers(json=false){const h={};if(json)h['Content-Type']='application/json';const t=$('token').value;if(t)h.Authorization='Bearer '+t;return h}
function show(e){if(e.type==='assistant.delta'){if(!answerEl){const row=document.createElement('div');row.className='event';const k=document.createElement('span');k.className='kind';k.textContent='[assistant] ';row.append(k);answerEl=document.createElement('span');answerEl.className='answer';row.append(answerEl);$('events').append(row)}answerEl.textContent+=e.text;answerEl.scrollIntoView();return}if(e.type==='assistant.message'&&answerEl)return;const row=document.createElement('div');row.className='event';const k=document.createElement('span');k.className='kind';k.textContent='['+e.type+'] ';row.append(k);const v=document.createElement('span');v.className=e.type==='run.failed'?'bad':(e.type==='assistant.message'?'answer':'');v.textContent=e.text||e.error||(e.type==='run.completed'?'complete':JSON.stringify(e));row.append(v);$('events').append(row);row.scrollIntoView()}
async function stream(id){let after=0;while(current===id){const r=await fetch(`${apiBase}/runs/${id}/events?after=${after}`,{headers:headers()});if(!r.ok)throw Error('stream HTTP '+r.status);const rd=r.body.getReader(),dec=new TextDecoder();let buf='';while(true){const x=await rd.read();if(x.done)break;buf+=dec.decode(x.value,{stream:true});const blocks=buf.split('\n\n');buf=blocks.pop();for(const block of blocks){if(!block||block[0]===':')continue;const dl=block.split('\n').find(l=>l.startsWith('data:'));if(!dl)continue;const e=JSON.parse(dl.slice(5));after=e.sequence;show(e);if(['run.completed','run.failed','run.cancelled'].includes(e.type)){current=null;$('status').textContent=e.type;$('cancel').disabled=true;return}}}}}
$('run').onclick=async()=>{try{$('events').textContent='';answerEl=null;$('status').textContent='starting';const r=await fetch(`${apiBase}/runs`,{method:'POST',headers:headers(true),body:JSON.stringify({provider:$('provider').value,prompt:$('prompt').value})});const d=await r.json();if(!r.ok)throw Error(d.error||'HTTP '+r.status);current=d.id;$('cancel').disabled=false;$('status').textContent='running '+d.id.slice(0,8);await stream(d.id)}catch(e){$('status').textContent=e.message;$('status').className='bad'}};
$('cancel').onclick=async()=>{if(current)await fetch(`${apiBase}/runs/${current}/cancel`,{method:'POST',headers:headers()})};
</script></main></body></html>"""


class WebApplication:
    def __init__(
        self,
        *,
        manager: RunManager,
        default_cwd: str,
        token: str,
        frame_ancestors: str,
    ) -> None:
        self.manager = manager
        self.default_cwd = default_cwd
        self.token = token
        self.frame_ancestors = frame_ancestors

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "h-agent-web/0.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                sys.stderr.write("h-agent web: " + fmt % args + "\n")

            def _headers(self, status: int, content_type: str, length: int | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", f"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors {application.frame_ancestors}")
                self.send_header("X-Content-Type-Options", "nosniff")
                if length is not None:
                    self.send_header("Content-Length", str(length))
                self.end_headers()

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode()
                self._headers(status, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if not application.token:
                    return True
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {application.token}"
                return hmac.compare_digest(supplied, expected)

            def _require_auth(self) -> bool:
                if self._authorized():
                    return True
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return False

            def _read_json(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ValueError("invalid content length") from exc
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    raise ValueError("request body must be between 1 byte and 1 MiB")
                try:
                    body = json.loads(self.rfile.read(length))
                except json.JSONDecodeError as exc:
                    raise ValueError("request body is not valid JSON") from exc
                if not isinstance(body, dict):
                    raise ValueError("request body must be a JSON object")
                return body

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path in {"/", "/ui", "/ui/"}:
                    body = _PAGE.encode()
                    self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                    self.wfile.write(body)
                    return
                if parsed.path == "/healthz":
                    self._json(HTTPStatus.OK, {"ok": True, "providers": sorted(PROVIDERS)})
                    return
                if not parsed.path.startswith("/api/") or not self._require_auth():
                    if not parsed.path.startswith("/api/"):
                        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    record = application.manager.get(parts[2])
                    if record is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
                    else:
                        self._json(HTTPStatus.OK, record.public())
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "events":
                    record = application.manager.get(parts[2])
                    if record is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
                        return
                    try:
                        after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                    except ValueError:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "after must be an integer"})
                        return
                    self._stream(record, after)
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def _stream(self, record: RunRecord, after: int) -> None:
                self._headers(HTTPStatus.OK, "text/event-stream; charset=utf-8")
                self.wfile.flush()
                cursor = after
                try:
                    while True:
                        events = record.wait_after(cursor, 3.0)
                        if events:
                            for event in events:
                                cursor = event["sequence"]
                                data = json.dumps(event, separators=(",", ":"))
                                block = f"id: {cursor}\nevent: {event['type']}\ndata: {data}\n\n"
                                self.wfile.write(block.encode())
                            self.wfile.flush()
                        else:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        if record.status in TERMINAL_STATES and not record.events_after(cursor):
                            break
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if not parsed.path.startswith("/api/") or not self._require_auth():
                    if not parsed.path.startswith("/api/"):
                        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                if parsed.path == "/api/runs":
                    try:
                        body = self._read_json()
                        record = application.manager.start(
                            provider=body.get("provider", "claude"),
                            prompt=body.get("prompt", ""),
                            # Workspace and behavioral options are operator
                            # settings, never fields an untrusted browser may
                            # override in a request.
                            cwd=application.default_cwd,
                        )
                    except (ValueError, ConfigurationError) as exc:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                        return
                    self._json(HTTPStatus.ACCEPTED, record.public())
                    return
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "cancel":
                    record = application.manager.cancel(parts[2])
                    if record is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
                    else:
                        self._json(HTTPStatus.ACCEPTED, record.public())
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        return Handler


def _frame_ancestors(value: str) -> str:
    origins = [item for item in value.split() if item]
    if not origins:
        return "'self'"
    for origin in origins:
        if origin == "'self'":
            continue
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ConfigurationError(f"invalid frame origin: {origin}")
    return " ".join(origins)


def _read_token(path: str | None) -> str:
    if not path:
        return os.environ.get("H_AGENT_WEB_TOKEN", "").strip()
    try:
        token = pathlib.Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"cannot read bearer token file {path}") from exc
    if not token:
        raise ConfigurationError(f"bearer token file is empty: {path}")
    return token


def _is_loopback(bind: str) -> bool:
    return bind in {"127.0.0.1", "::1", "localhost"}


def _prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    if sys.stdin.isatty():
        raise ConfigurationError("provide --prompt TEXT or pipe a prompt on stdin")
    return sys.stdin.read()


def command_run(args: argparse.Namespace) -> int:
    servers = load_http_mcp_servers() if args.mcp else {}
    manager = RunManager(mcp_servers=servers)
    record = manager.start(
        provider=args.provider,
        prompt=_prompt(args),
        cwd=args.cwd,
        options={
            key: value
            for key, value in {
                "model": args.model,
                "max_turns": args.max_turns,
                "max_budget_usd": args.max_budget_usd,
                "system_prompt": args.system_prompt,
            }.items()
            if value is not None
        },
    )
    cursor = 0
    while True:
        events = record.wait_after(cursor, 1.0)
        for event in events:
            cursor = event["sequence"]
            print(json.dumps(event, separators=(",", ":")), flush=True)
        if record.status in TERMINAL_STATES and not record.events_after(cursor):
            return 0 if record.status == "completed" else 1


def command_serve(args: argparse.Namespace) -> int:
    token = _read_token(args.token_file)
    if not _is_loopback(args.bind) and not token and not args.allow_unauthenticated:
        raise ConfigurationError(
            "refusing unauthenticated non-loopback bind; set H_AGENT_WEB_TOKEN, "
            "use --token-file, or explicitly pass --allow-unauthenticated behind an auth proxy"
        )
    servers = load_http_mcp_servers() if args.mcp else {}
    manager = RunManager(mcp_servers=servers)
    app = WebApplication(
        manager=manager,
        default_cwd=str(pathlib.Path(args.cwd).expanduser().resolve()),
        token=token,
        frame_ancestors=_frame_ancestors(args.frame_origin),
    )
    server = ThreadingHTTPServer((args.bind, args.port), app.handler_class())
    server.daemon_threads = True
    print(
        f"h-agent web listening on http://{args.bind}:{server.server_port}/ui "
        f"(providers: claude,codex; http MCP servers: {len(servers)})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="h-agent web", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one provider and stream NDJSON to stdout")
    run.add_argument("provider", choices=sorted(PROVIDERS))
    run.add_argument("--prompt")
    run.add_argument("--cwd", default=os.getcwd())
    run.add_argument("--model")
    run.add_argument("--max-turns", type=int)
    run.add_argument("--max-budget-usd", type=float)
    run.add_argument("--system-prompt")
    run.add_argument(
        "--mcp", action="store_true",
        help="opt in to HTTP servers in h-agent's MCP registry",
    )
    run.set_defaults(func=command_run)

    serve = sub.add_parser("serve", help="serve the embeddable UI and JSON/SSE API")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    serve.add_argument("--cwd", default=os.getcwd())
    serve.add_argument("--frame-origin", default="'self'")
    serve.add_argument("--token-file")
    serve.add_argument("--allow-unauthenticated", action="store_true")
    serve.add_argument(
        "--mcp", action="store_true",
        help="opt in to HTTP servers in h-agent's MCP registry",
    )
    serve.set_defaults(func=command_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigurationError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    except Exception:
        if os.environ.get("H_AGENT_WEB_DEBUG") == "1":
            traceback.print_exc()
        else:
            print("error: unexpected h-agent web failure (set H_AGENT_WEB_DEBUG=1 for details)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
