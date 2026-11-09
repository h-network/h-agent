# SDK web continuity handoff

This document is the recovery contract for h-agent's deployment-neutral Claude
and Codex web runner. It intentionally contains no deployment hostname, MCP
endpoint, bearer value, provider credential, or infrastructure-specific
default. Those belong in the operator's external deployment configuration.

## Source of truth

- Repository: `h-network/h-agent`
- Branch: `mesh-agent/sdk-web-claude-codex`
- Last source checkpoint before this handoff: `125d722`
- Providers proven: Claude Code `2.1.311` and Codex CLI `0.166.0`
- Optional Python dependencies: `requirements-sdk-web.txt`

The branch includes the launcher (`h-app/h-agent-web`), implementation
(`h-app/h_agent_web.py`), installer and Makefile integration, tests, and user
documentation. Restore from the remote branch, not from an old image or an
uncommitted workspace copy.

## Runtime contract

Install h-agent and its optional Python dependencies in the runtime
environment, provide already-authenticated provider homes, then start:

```sh
export H_AGENT_PYTHON=/path/to/venv/bin/python
export AGENT_MCP_FILE=/run/config/h-agent-mcp.json
h-agent web serve \
  --bind 0.0.0.0 \
  --port 8090 \
  --cwd /workspace \
  --token-file /run/secrets/web-token \
  --mcp
```

`--mcp` is optional and off by default. When enabled, the registry must contain
only operator-approved HTTP MCP entries. Stdio entries are ignored. Registry
bearers are resolved at runtime from `token_file` or `token_env`; do not place
their values in Git, images, arguments, or logs.

Required persistent provider state is outside this repository:

- Claude's authenticated home/configuration.
- Codex's authenticated `CODEX_HOME`.
- The web API bearer file.
- The optional MCP registry and its referenced bearer files.

The service itself does not require a writable workspace. The proven
deployment used an unprivileged identity, a read-only root and workspace,
dropped capabilities, no privilege escalation, and no container-engine socket.
Keep equivalent containment after recovery.

## HTTP contract

- `GET /healthz` is an unauthenticated liveness check.
- `GET /ui` serves the small embeddable client.
- `POST /api/runs` accepts `provider` and `prompt`.
- `GET /api/runs/{id}` reports status and the terminal result.
- `GET /api/runs/{id}/events?after={sequence}` streams resumable SSE.
- `POST /api/runs/{id}/cancel` requests cooperative cancellation.

All `/api` endpoints require the configured bearer. A non-loopback bind is
refused without a bearer unless an operator explicitly declares an external
authentication proxy with `--allow-unauthenticated`. Configure
`--frame-origin` when a different origin embeds `/ui`.

## Security boundary

- Claude receives no built-in tools (`tools=[]`).
- Codex local shell and unified-exec features are disabled, approvals remain
  globally denied, and the sandbox is read-only.
- Codex's code-mode host remains available because code-mode-only models need
  it to dispatch remote MCP calls. It does not restore the removed local tools.
- Tools on an MCP server explicitly selected with `--mcp` are preapproved for
  unattended Codex use. The MCP credential's server-side tools and scopes are
  therefore the authorization boundary; provision it with least privilege.
- Raw tool inputs, results, and credentials are not copied into API events.

## State and compaction semantics

The current web service is job-oriented, not a persistent chat service. Each
`POST /api/runs` creates a new in-memory `RunRecord`:

- Claude starts a new query. Its provider session ID is emitted at completion
  but is not resumed by a later request.
- Codex starts a new ephemeral thread.
- Run events and results live only in service memory and disappear on restart.
- Provider-managed compaction can occur inside an unusually long individual
  run, but no compacted conversation is carried into the next web request.

This avoids unbounded conversation context and token growth. Persistent
multi-turn conversations would be a separate opt-in feature requiring durable
conversation records, provider session/thread resumption, retention limits,
and explicit context/compaction events.

## Recovery verification

After restoring the runtime, verify in this order without printing secrets:

1. `GET /healthz` returns `200`.
2. Missing and incorrect bearers on `POST /api/runs` return `401`.
3. One authenticated Claude baseline run reaches `run.completed` with a fixed
   marker.
4. One authenticated Codex baseline run does the same.
5. If MCP is enabled, each provider invokes one benign read/search tool and
   reaches `run.completed` with a fixed marker.
6. If the MCP credential intentionally permits remote execution, each provider
   invokes a harmless fixed-output command and returns the exact marker.
7. Recheck the listener boundary, runtime UID, read-only mounts, dropped
   capabilities, absence of privileged sockets, and secret file modes.

The completed proof passed all six provider paths: Claude and Codex baseline,
remote search, and scoped remote execution. The only known non-blocking issue
is that Claude's SDK stream can emit `tool.started` without `tool.completed`
even when the remote call and terminal result succeed.

## Source verification

Run all h-agent test executables before handing off a new commit:

```sh
for test in h-app/test/*test; do
  "$test"
done
```

At the last source checkpoint, six tests were collected and all six passed.

