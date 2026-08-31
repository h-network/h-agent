# h-agent

`h-agent` launches Claude Code, Agy, or Codex with consistent unattended-mode
flags. It can also point Claude Code at a local inference endpoint.

The wrapper is standalone: it does not create a user, change identity, or
assume a particular username or home path. The selected CLI runs as the caller
and inherits that caller's `HOME` and configuration files.

## Install

Install for the current user with one command:

```sh
curl -fsSL https://raw.githubusercontent.com/h-network/h-agent/main/install.sh | bash
```

This installs `h-agent` plus its compatible CLI versions—Claude Code `2.1.251`,
Codex CLI `0.149.0`, and agy `1.1.22`—using their official installers. Already
installed CLIs at those exact versions are left untouched. Installation does
not require `sudo`.

The installer also seeds unattended defaults in the invoking identity's
`$HOME`: Claude's completed-onboarding state and settings, plus Codex's update,
approval, and sandbox defaults. Missing values are added, but existing user
values are never replaced. Project trust is granted only to the directory from
which the installer is run. The installer does not create or switch users and
works the same for root or any provisioned user/UID.

Choose a different h-agent prefix or stage a package by setting the same
variables supported by the Makefile:

```sh
curl -fsSL https://raw.githubusercontent.com/h-network/h-agent/main/install.sh | \
  PREFIX=/opt/h-agent DESTDIR=/tmp/package-root bash
```

Set `H_AGENT_VERSION` to install another Git ref, `H_AGENT_REPOSITORY` to use a
fork, or `H_AGENT_INSTALL_URL` to download `h-agent` itself from a custom URL.
`seedProfile`, `setupConfigDir`, and their shared library download as siblings
of that URL by default (same directory, their own filenames); override them
individually with `H_AGENT_SEEDPROFILE_URL`, `H_AGENT_SETUPCONFIGDIR_URL`, and
`H_AGENT_PROFILELIB_URL` if they live elsewhere.
Set `H_AGENT_INSTALL_CLIS=0` to install only the h-agent scripts, without any
CLI. When `DESTDIR` is set, the installer stages only those scripts and never
changes the invoking user's CLI installations or configuration. Set
`H_AGENT_SEED_CONFIG=0` to leave CLI configuration untouched. Merging defaults
into existing Claude JSON requires `jq`; without it, existing files are
preserved and a warning is printed.

System-wide (typically requires elevated permissions):

```sh
make install
```

For one user, choose a prefix inside that user's home directory:

```sh
make install PREFIX="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
```

Packagers can set `DESTDIR`, `PREFIX`, or `bindir` independently. To stage a
package, for example:

```sh
make install DESTDIR=/tmp/package-root PREFIX=/usr
```

The target machine must provide Bash 4 or newer. A Makefile installation does
not install the agent CLIs; use the one-line installer above or provide a
supported CLI on `PATH`.

## Use

```sh
h-agent                 # AGENT_CLI, or claude by default
h-agent claude --resume
h-agent agy
h-agent codex
h-agent probeProvider http://localhost:8000
h-agent probeProvider http://localhost:8000 my-model
h-agent --help
```

Approval prompts are skipped by default. This is intended for externally
sandboxed environments. Set `AGENT_SKIP_PERMISSIONS=0` when that is not
appropriate.

All configuration is supplied at runtime, so the same installation works for
any account and home directory:

- `AGENT_CLI`: default CLI (`claude`)
- `AGENT_SKIP_PERMISSIONS`: `1` to add unattended-mode flags (default), `0` not to
- `AGENT_CLAUDE_TOOLS`: space-separated Claude tool list; empty means unrestricted
- `AGENT_PROVIDER_URL`: local endpoint URL (Claude only)
- `AGENT_PROVIDER_MODEL`: model exposed by the endpoint
- `AGENT_PROVIDER_SMALL_MODEL`: optional smaller model
- `AGENT_PROVIDER_TOKEN`: optional endpoint token

### Probe a local provider

`h-agent probeProvider <url> [model-id]` checks that a local endpoint is
actually usable by Claude Code. It discovers models from the OpenAI-compatible
`/v1/models` route, falling back to Ollama's `/api/tags`, and then sends a real
Anthropic-protocol request to `/v1/messages`. Passing a model ID verifies that
the endpoint lists that exact model before probing it; otherwise the first
discovered model is used.

The probe distinguishes a cold-load timeout, a missing Anthropic route, and an
HTTP response that is not an Anthropic message. On success it prints shell
exports for `AGENT_PROVIDER_URL` and `AGENT_PROVIDER_MODEL` (and
`AGENT_PROVIDER_TOKEN` when supplied). The command exits 0 when verified, 1
when unusable, and 2 for invalid usage.

The diagnostic requires `curl` and `jq`. Model listing uses a 10-second
timeout. Set `PROBE_TIMEOUT` to change the message-probe timeout from its
90-second default, and set `AGENT_PROVIDER_TOKEN` when the endpoint requires an
API key:

```sh
PROBE_TIMEOUT=180 AGENT_PROVIDER_TOKEN=local-secret \
  h-agent probeProvider http://localhost:8000/v1
```

### Named profiles

By default every CLI shares one profile at `$HOME/.claude` / `$HOME/.codex`.
Two commands, installed alongside `h-agent`, create isolated, named copies of
that default profile — for running multiple concurrent agents on one host
without them sharing config, skills, or (unless asked) logins. Neither ever
overwrites a file that already exists in the target profile, so re-running
either is always safe.

`setupConfigDir` is interactive: it prints `export`/`unset` lines for you to
`eval`, so it can switch your current shell.

```sh
eval "$(setupConfigDir work)"     # ~/.claude-work + ~/.codex-work, no login
eval "$(setupConfigDir work --same)"  # ...and copy today's logins across
eval "$(setupConfigDir default)"  # back to the shared ~/.claude + ~/.codex
setupConfigDir                    # show what this shell is currently on
```

`seedProfile` is noninteractive, for subprocesses and automation — one CLI
per call, printing the resulting variable rather than exporting it itself.
Login credentials are never copied; a seeded profile is unauthenticated by
design.

```sh
seedProfile claude ci-run           # CLAUDE_CONFIG_DIR=/home/you/.claude-ci-run
seedProfile --export codex ci-run   # export CODEX_HOME=/home/you/.codex-ci-run
```

Both validate the profile name (no path traversal, alphanumeric/`.`/`_`/`-`
only) and refuse `agy`: it has no config-dir override of its own (it reads
`~/.gemini/antigravity-cli` straight off `HOME`), so isolating it would mean
splitting `HOME` entirely rather than pointing an env var elsewhere.

## Develop

```sh
make test
shellcheck h-agent install.sh seedProfile setupConfigDir \
    h-agent-profile-lib.sh test/h-agent-test test/install-test \
    test/profile-test
```

## License

Apache-2.0
