# h-agent

`h-agent` launches Claude Code, Agy, or Codex with consistent unattended-mode
flags. It can also point Claude Code at a local inference endpoint.

The wrapper is standalone: it does not create a user, change identity, or
assume a particular username or home path. The selected CLI runs as the caller
and inherits that caller's `HOME` and configuration files.

## Install

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

The target machine must provide Bash 4 or newer and at least one supported
agent CLI on `PATH`.

## Use

```sh
h-agent                 # AGENT_CLI, or claude by default
h-agent claude --resume
h-agent agy
h-agent codex
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

## Develop

```sh
make test
shellcheck h-agent test/h-agent-test
```

## License

Apache-2.0
