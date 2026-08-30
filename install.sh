#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

repo=${H_AGENT_REPOSITORY:-h-network/h-agent}
version=${H_AGENT_VERSION:-main}
url=${H_AGENT_INSTALL_URL:-https://raw.githubusercontent.com/$repo/$version/h-agent}
destdir=${DESTDIR:-}
claude_version=2.1.251
codex_version=0.149.0
agy_version=1.1.22

if [ -n "${PREFIX:-}" ]; then
    prefix=$PREFIX
elif [ -n "${HOME:-}" ]; then
    prefix=${HOME%/}/.local
else
    detected_home=''
    if command -v getent >/dev/null 2>&1 && command -v id >/dev/null 2>&1; then
        detected_home=$(getent passwd "$(id -u)" | cut -d: -f6)
    fi
    if [ -z "$detected_home" ]; then
        echo 'error: cannot determine a user-writable install prefix' >&2
        echo '       set PREFIX (for example, PREFIX=/path/to/.local)' >&2
        exit 2
    fi
    prefix=${detected_home%/}/.local
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

download_file "$url" "$download"

if [ ! -s "$download" ] || ! head -n 1 "$download" | grep -q '^#!/usr/bin/env bash$'; then
    echo "error: download from $url is not an h-agent executable" >&2
    exit 1
fi

# DESTDIR means package staging: never mutate the invoking user's CLI installs.
if [ -z "$destdir" ] && [ "${H_AGENT_INSTALL_CLIS:-1}" = 1 ]; then
    install_claude
    install_codex
    install_agy
fi

install -d "$bindir"
install -m 0755 "$download" "$bindir/h-agent"

echo "installed h-agent to $bindir/h-agent"
case ":${PATH:-}:" in
    *":${prefix%/}/bin:"*) ;;
    *) echo "add ${prefix%/}/bin to PATH to run h-agent" ;;
esac
