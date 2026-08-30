#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

repo=${H_AGENT_REPOSITORY:-h-network/h-agent}
version=${H_AGENT_VERSION:-main}
url=${H_AGENT_INSTALL_URL:-https://raw.githubusercontent.com/$repo/$version/h-agent}
destdir=${DESTDIR:-}

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

if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$download"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$download" "$url"
else
    echo 'error: installing h-agent requires curl or wget' >&2
    exit 127
fi

if [ ! -s "$download" ] || ! head -n 1 "$download" | grep -q '^#!/usr/bin/env bash$'; then
    echo "error: download from $url is not an h-agent executable" >&2
    exit 1
fi

install -d "$bindir"
install -m 0755 "$download" "$bindir/h-agent"

echo "installed h-agent to $bindir/h-agent"
case ":${PATH:-}:" in
    *":${prefix%/}/bin:"*) ;;
    *) echo "add ${prefix%/}/bin to PATH to run h-agent" ;;
esac
