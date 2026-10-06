#!/usr/bin/env bash
# resign.sh — tauri updater re-sign, run from bash.
#
# Why bash: an EMPTY-string environment variable (TAURI_SIGNING_PRIVATE_KEY_PASSWORD="")
# can only be exported from bash. PowerShell and cmd both DELETE a variable when
# you assign an empty string (PS `$env:X=""` and .NET SetEnvironmentVariable(name,"")
# remove it), so from those shells tauri prompts for a passphrase and hangs
# forever in headless runs. This is the single reason this file exists.
#
# Usage: bash scripts/resign.sh <absolute-path-to-exe>
#   Signs exactly the file given (the .sig records the file name — always sign
#   the underscore-name copy that goes to R2, not the space-name local artifact).
set -euo pipefail

exe="$1"
key_file="$HOME/.tauri-keys/aiming-cookie.key"
frontend="$(cd "$(dirname "$0")/../webapp/frontend" && pwd)"

if [ ! -f "$exe" ]; then echo "resign: target not found: $exe" >&2; exit 1; fi
if [ ! -f "$key_file" ]; then echo "resign: key not found: $key_file" >&2; exit 1; fi

export TAURI_SIGNING_PRIVATE_KEY="$(tr -d '\r' < "$key_file")"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

cd "$frontend"
npx tauri signer sign "$exe"
