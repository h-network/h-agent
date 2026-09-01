#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 h-network
#
# Shared validation/copy/state logic for h-agent's named-profile commands
# (setupConfigDir, seedProfile). Meant to be sourced, not executed.

hap_validate_profile_name() {
    # Prints the normalized name on stdout; writes an error to stderr and
    # returns non-zero on a bad name.
    local raw=$1 name
    case "$raw" in
        *..*)
            echo "error: '..' is not allowed in a profile name (got '$raw')" >&2
            return 2
            ;;
    esac
    name=${raw%/}
    name=${name##*/}
    name=${name#.claude-}
    name=${name#.codex-}
    case "$name" in
        ""|*[!a-zA-Z0-9._-]*)
            echo "error: profile name must be alphanumeric, '.', '_' or '-' (got '$raw')" >&2
            return 2
            ;;
    esac
    printf '%s\n' "$name"
}

hap_copy_if_absent() {
    local src=$1 dst=$2
    [ -e "$dst" ] && return 0
    [ -e "$src" ] || return 0
    cp -r "$src" "$dst"
}

hap_copy_profile_assets() {
    # hap_copy_profile_assets <src_dir> <dst_dir> <item>...
    local src_dir=$1 dst_dir=$2 item
    shift 2
    mkdir -p "$dst_dir" || return 1
    [ -d "$src_dir" ] || return 0
    for item in "$@"; do
        hap_copy_if_absent "$src_dir/$item" "$dst_dir/$item"
    done
}

hap_seed_claude_onboarding() {
    # Minimal onboarding-only seed, no trust scoping.
    local dst_dir=$1
    [ -f "$dst_dir/.claude.json" ] && return 0
    printf '{\n  "hasCompletedOnboarding": true\n}\n' >"$dst_dir/.claude.json"
}

hap_seed_claude_onboarding_scoped() {
    # hap_seed_claude_onboarding_scoped <dst_dir> <source_json> <trust_path>
    #
    # jq merge that pre-trusts only one fixed project path (base's runtime
    # always sandboxes agents at /workspace), never overwriting a profile's
    # existing .claude.json.
    local dst_dir=$1 source_json=$2 trust_path=$3
    [ -f "$dst_dir/.claude.json" ] && return 0
    command -v jq >/dev/null 2>&1 || {
        echo "error: seeding claude onboarding state requires jq" >&2
        return 1
    }
    { [ -s "$source_json" ] && cat "$source_json" || echo '{}'; } \
        | jq --arg path "$trust_path" \
            '{hasCompletedOnboarding: true,
              projects: (.projects // {} | with_entries(
                  select(.key == $path)
                  | .value |= {hasTrustDialogAccepted:
                                   (if .hasTrustDialogAccepted == null then true else .hasTrustDialogAccepted end),
                               hasCompletedProjectOnboarding:
                                   (if .hasCompletedProjectOnboarding == null then true else .hasCompletedProjectOnboarding end)}))}' \
        >"$dst_dir/.claude.json"
}

hap_copy_credential() {
    local src=$1 dst=$2
    [ -f "$dst" ] && return 0
    if [ -f "$src" ]; then
        cp "$src" "$dst" && chmod 600 "$dst"
    else
        return 1
    fi
}

hap_agy_refusal() {
    echo "error: agy has no config-dir override" >&2
    echo "       it reads ~/.gemini/antigravity-cli/ relative to HOME, and its" >&2
    echo "       credential lives at ~/.gemini/antigravity-cli/antigravity-oauth-token," >&2
    echo "       separate from claude's and codex's rather than shared with them." >&2
    echo "       Separating agy profiles means separating HOME, which is a bigger" >&2
    echo "       change than this command should make silently." >&2
}
