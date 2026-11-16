#!/usr/bin/env bash
# Sourced by sync.sh and scheduled-sync.sh. Reads KEY=VALUE lines from .env WITHOUT executing the file as shell.
#   - blank lines and lines starting with # are ignored; an optional leading "export " is allowed
#   - values may be wrapped in single or double quotes; no inline comments (put comments on their own line)
#   - a leading ~ and $HOME are expanded; nothing else is
#   - variables already set in the real environment win over the file

load_env() {
  local file="$1" line key val dq='^"(.*)"$' sq="^'(.*)'\$"
  [[ -f "$file" ]] || { echo "missing $file -- copy .env.example to .env and fill it in" >&2; return 1; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    line="${line#export }"
    [[ "$line" == *=* ]] || { echo "bad line in $file (expected KEY=VALUE): $line" >&2; return 1; }
    key="${line%%=*}"; key="${key//[[:space:]]/}"; val="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "bad variable name in $file: $key" >&2; return 1; }
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "$val" =~ $dq ]]; then val="${BASH_REMATCH[1]}"; elif [[ "$val" =~ $sq ]]; then val="${BASH_REMATCH[1]}"; fi
    val="${val/#\~/$HOME}"; val="${val//\$HOME/$HOME}"
    [[ -z "${!key+x}" ]] && export "$key=$val"
  done < "$file"
  return 0
}

# require_vars VAR... : fail with the list of variables that are unset or empty.
require_vars() {
  local v missing=()
  for v in "$@"; do [[ -n "${!v:-}" ]] || missing+=("$v"); done
  [[ ${#missing[@]} -eq 0 ]] || { echo "missing in .env: ${missing[*]}" >&2; return 1; }
}
