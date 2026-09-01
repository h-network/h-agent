#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail

repo=${H_AGENT_REPOSITORY:-h-network/h-agent}
version=${H_AGENT_VERSION:-main}
url=${H_AGENT_INSTALL_URL:-https://raw.githubusercontent.com/$repo/$version/h-agent}
destdir=${DESTDIR:-}
claude_version=2.1.251
codex_version=0.149.0
agy_version=1.1.23

if [ -z "${HOME:-}" ]; then
    if command -v getent >/dev/null 2>&1 && command -v id >/dev/null 2>&1; then
        HOME=$(getent passwd "$(id -u)" | cut -d: -f6)
        export HOME
    fi
    if [ -z "${HOME:-}" ]; then
        echo "error: cannot determine the invoking identity's home directory" >&2
        echo '       set HOME to the directory this identity should use' >&2
        exit 2
    fi
fi

if [ -n "${PREFIX:-}" ]; then
    prefix=$PREFIX
else
    prefix=${HOME%/}/.local
fi

case "$prefix" in
    /*) ;;
    *) echo "error: PREFIX must be an absolute path: $prefix" >&2; exit 2 ;;
esac
case "$destdir" in
    ""|/*) ;;
    *) echo "error: DESTDIR must be empty or an absolute path: $destdir" >&2; exit 2 ;;
esac

bindir=${destdir}${prefix%/}/bin
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/h-agent-install.XXXXXXXX")
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM
download=$tmpdir/h-agent

download_file() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        echo 'error: installing h-agent requires curl or wget' >&2
        exit 127
    fi
}

download_stdout() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1"
    else
        wget -qO - "$1"
    fi
}

installed_version_is() {
    local expected=${2//./\\.}
    [ -x "$1" ] && "$1" --version 2>/dev/null | grep -Eq "(^|[[:space:]])$expected([[:space:]]|$)"
}

install_claude() {
    local binary=$HOME/.local/bin/claude installer=$tmpdir/claude-install.sh
    if installed_version_is "$binary" "$claude_version"; then
        echo "Claude Code $claude_version is already installed"
        return
    fi
    download_file https://claude.ai/install.sh "$installer"
    bash "$installer" "$claude_version"
    installed_version_is "$binary" "$claude_version" || {
        echo "error: Claude Code $claude_version installation could not be verified" >&2
        exit 1
    }
}

install_codex() {
    local binary=$HOME/.local/bin/codex installer=$tmpdir/codex-install.sh
    if installed_version_is "$binary" "$codex_version"; then
        echo "Codex CLI $codex_version is already installed"
        return
    fi
    download_file https://chatgpt.com/codex/install.sh "$installer"
    CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR="$HOME/.local/bin" \
        sh "$installer" --release "$codex_version"
    installed_version_is "$binary" "$codex_version" || {
        echo "error: Codex CLI $codex_version installation could not be verified" >&2
        exit 1
    }
}

install_agy() {
    local binary=$HOME/.local/bin/agy installer=$tmpdir/agy-install.sh
    local os arch platform manifest manifest_version
    if installed_version_is "$binary" "$agy_version"; then
        echo "agy $agy_version is already installed"
        return
    fi
    case "$(uname -s)" in
        Linux) os=linux ;;
        Darwin) os=darwin ;;
        *) echo "error: agy does not support $(uname -s)" >&2; exit 1 ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64) arch=amd64 ;;
        arm64|aarch64) arch=arm64 ;;
        *) echo "error: agy does not support architecture $(uname -m)" >&2; exit 1 ;;
    esac
    platform=${os}_${arch}
    if [ "$os" = linux ] && { [ -f /lib/libc.musl-x86_64.so.1 ] || \
        [ -f /lib/libc.musl-aarch64.so.1 ] || ldd /bin/ls 2>&1 | grep -q musl; }; then
        platform=${platform}_musl
    fi
    manifest=$(download_stdout "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/$platform.json")
    manifest_version=$(printf '%s\n' "$manifest" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    if [ "$manifest_version" != "$agy_version" ]; then
        echo "error: agy's official installer currently offers ${manifest_version:-an unknown version}," >&2
        echo "       but h-agent requires $agy_version; refusing an incompatible install" >&2
        exit 1
    fi
    download_file https://antigravity.google/cli/install.sh "$installer"
    bash "$installer" --dir "$HOME/.local/bin"
    installed_version_is "$binary" "$agy_version" || {
        echo "error: agy $agy_version installation could not be verified" >&2
        exit 1
    }
}

print_path_instruction() {
    local shell_name config command_path
    shell_name=${SHELL:-}
    shell_name=${shell_name##*/}
    case "$shell_name" in
        bash) config=$HOME/.bashrc ;;
        zsh) config=$HOME/.zshrc ;;
        fish) config=$HOME/.config/fish/config.fish ;;
        ksh) config=$HOME/.kshrc ;;
        *)
            if [ -e "$HOME/.bashrc" ]; then
                config=$HOME/.bashrc
            elif [ -e "$HOME/.zshrc" ]; then
                config=$HOME/.zshrc
            else
                config=$HOME/.profile
            fi
            ;;
    esac

    command_path=${prefix%/}/bin
    config=${config/#$HOME/\$HOME}
    command_path=${command_path/#$HOME/\$HOME}
    if [ "$shell_name" = fish ]; then
        printf 'echo '\''fish_add_path "%s"'\'' >> "%s" && source "%s"\n' \
            "$command_path" "$config" "$config"
    else
        # $PATH must remain literal in the command copied into the shell file.
        # shellcheck disable=SC2016
        printf 'echo '\''export PATH="%s:$PATH"'\'' >> "%s" && source "%s"\n' \
            "$command_path" "$config" "$config"
    fi
}

seed_claude_state() {
    local target=$HOME/.claude.json tmp escaped_pwd
    if [ -s "$target" ]; then
        if ! command -v jq >/dev/null 2>&1; then
            echo "warning: preserving existing $target; install jq to merge h-agent defaults" >&2
            return
        fi
        tmp=$(mktemp "$HOME/.claude.json.tmp.XXXXXXXX")
        if ! jq --arg cwd "$PWD" \
            '{hasCompletedOnboarding: true,
              projects: {($cwd): {
                hasTrustDialogAccepted: true,
                hasCompletedProjectOnboarding: true
              }}} * .' "$target" >"$tmp"; then
            rm -f "$tmp"
            echo "error: existing $target is not valid JSON; leaving it unchanged" >&2
            exit 1
        fi
        mv "$tmp" "$target"
        return
    fi
    escaped_pwd=${PWD//\\/\\\\}
    escaped_pwd=${escaped_pwd//\"/\\\"}
    escaped_pwd=${escaped_pwd//$'\n'/\\n}
    printf '{\n  "hasCompletedOnboarding": true,\n  "projects": {\n    "%s": {\n      "hasTrustDialogAccepted": true,\n      "hasCompletedProjectOnboarding": true\n    }\n  }\n}\n' \
        "$escaped_pwd" >"$target"
}

seed_claude_settings() {
    local target=$HOME/.claude/settings.json tmp
    mkdir -p "$HOME/.claude"
    if [ -s "$target" ]; then
        if ! command -v jq >/dev/null 2>&1; then
            echo "warning: preserving existing $target; install jq to merge h-agent defaults" >&2
            return
        fi
        tmp=$(mktemp "$HOME/.claude/settings.json.tmp.XXXXXXXX")
        if ! jq '
            {promptSuggestionEnabled: false,
             awaySummaryEnabled: false,
             preferredNotifChannel: "notifications_disabled",
             fileCheckpointingEnabled: false,
             agentPushNotifEnabled: false,
             skipDangerousModePermissionPrompt: true,
             disableBundledSkills: true,
             enableAllProjectMcpServers: false,
             attribution: {commit: "", pr: "", sessionUrl: false},
             permissions: {deny: ["mcp__*"]},
             env: {
               DISABLE_TELEMETRY: "1",
               DISABLE_ERROR_REPORTING: "1",
               DISABLE_FEEDBACK_COMMAND: "1",
               CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY: "1"
             }} * .' "$target" >"$tmp"; then
            rm -f "$tmp"
            echo "error: existing $target is not valid JSON; leaving it unchanged" >&2
            exit 1
        fi
        mv "$tmp" "$target"
        return
    fi
    cat >"$target" <<'EOF'
{
  "promptSuggestionEnabled": false,
  "awaySummaryEnabled": false,
  "preferredNotifChannel": "notifications_disabled",
  "fileCheckpointingEnabled": false,
  "agentPushNotifEnabled": false,
  "skipDangerousModePermissionPrompt": true,
  "disableBundledSkills": true,
  "enableAllProjectMcpServers": false,
  "attribution": {"commit": "", "pr": "", "sessionUrl": false},
  "permissions": {"deny": ["mcp__*"]},
  "env": {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_FEEDBACK_COMMAND": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1"
  }
}
EOF
}

codex_has_top_level_key() {
    awk -v key="$2" '
        /^[[:space:]]*\[/ { in_table = 1 }
        !in_table && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" { found = 1 }
        END { exit found ? 0 : 1 }
    ' "$1"
}

seed_codex_config() {
    local target=$HOME/.codex/config.toml tmp additions=0
    mkdir -p "$HOME/.codex"
    if [ ! -e "$target" ]; then
        cat >"$target" <<'EOF'
check_for_update_on_startup = false
approval_policy = "never"
sandbox_mode = "danger-full-access"
EOF
        return
    fi
    tmp=$(mktemp "$HOME/.codex/config.toml.tmp.XXXXXXXX")
    if ! codex_has_top_level_key "$target" check_for_update_on_startup; then
        echo 'check_for_update_on_startup = false' >>"$tmp"
        additions=1
    fi
    if ! codex_has_top_level_key "$target" approval_policy; then
        echo 'approval_policy = "never"' >>"$tmp"
        additions=1
    fi
    if ! codex_has_top_level_key "$target" sandbox_mode; then
        echo 'sandbox_mode = "danger-full-access"' >>"$tmp"
        additions=1
    fi
    if [ "$additions" = 1 ]; then
        echo >>"$tmp"
        cat "$target" >>"$tmp"
        mv "$tmp" "$target"
    else
        rm -f "$tmp"
    fi
}

seed_cli_config() {
    umask 077
    mkdir -p "$HOME"
    seed_claude_state
    seed_claude_settings
    seed_codex_config
}
base_download_url=${url%/h-agent}
seedprofile_url=${H_AGENT_SEEDPROFILE_URL:-$base_download_url/seedProfile}
setupconfigdir_url=${H_AGENT_SETUPCONFIGDIR_URL:-$base_download_url/setupConfigDir}
profilelib_url=${H_AGENT_PROFILELIB_URL:-$base_download_url/h-agent-profile-lib.sh}

download_verified() {
    local from=$1 to=$2
    download_file "$from" "$to"
    if [ ! -s "$to" ] || ! head -n 1 "$to" | grep -q '^#!/usr/bin/env bash$'; then
        echo "error: download from $from is not an h-agent script" >&2
        exit 1
    fi
}

download_verified "$url" "$download"
download_verified "$seedprofile_url" "$tmpdir/seedProfile"
download_verified "$setupconfigdir_url" "$tmpdir/setupConfigDir"
download_verified "$profilelib_url" "$tmpdir/h-agent-profile-lib.sh"

# DESTDIR means package staging: never mutate the invoking user's CLI installs.
if [ -z "$destdir" ] && [ "${H_AGENT_INSTALL_CLIS:-1}" = 1 ]; then
    install_claude
    install_codex
    install_agy
fi
if [ -z "$destdir" ] && [ "${H_AGENT_SEED_CONFIG:-1}" = 1 ]; then
    seed_cli_config
fi

install -d "$bindir"
install -m 0755 "$download" "$bindir/h-agent"
install -m 0755 "$tmpdir/seedProfile" "$bindir/seedProfile"
install -m 0755 "$tmpdir/setupConfigDir" "$bindir/setupConfigDir"
install -m 0755 "$tmpdir/h-agent-profile-lib.sh" "$bindir/h-agent-profile-lib.sh"

echo "installed h-agent to $bindir/h-agent"
if [ -z "$destdir" ]; then
    case ":${PATH:-}:" in
        *":${prefix%/}/bin:"*) ;;
        *)
            echo 'Run this command to add h-agent to PATH:'
            print_path_instruction
            ;;
    esac
fi
