#!/usr/bin/env bash
# setup.sh — one-shot install for dj-edit.
# Linux (Arch / Debian / Ubuntu) or macOS (via Homebrew). Idempotent.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd -P)"

OS="$(uname -s)"
echo "[setup] os=$OS root=$ROOT"

# 1) System dependencies
have() { command -v "$1" >/dev/null 2>&1; }

install_brew() {
    if have brew; then return; fi
    echo "[setup] Homebrew not found. Install from https://brew.sh and re-run." >&2
    exit 1
}

case "$OS" in
    Darwin)
        install_brew
        for pkg in ffmpeg python@3.11; do
            if ! brew list "$pkg" >/dev/null 2>&1; then
                echo "[setup] brew install $pkg"
                brew install "$pkg"
            else
                echo "[setup] brew $pkg already installed"
            fi
        done
        # macOS ships bash 3.2; render.sh now portable but warn if user wants 4+ features.
        BREW_PY="$(brew --prefix python@3.11)/bin/python3.11"
        if [ ! -x "$BREW_PY" ]; then BREW_PY="$(command -v python3.11 || command -v python3)"; fi
        ;;
    Linux)
        # Detect package manager
        if have pacman; then
            for pkg in ffmpeg python jq; do
                pacman -Qi "$pkg" >/dev/null 2>&1 || sudo pacman -S --noconfirm "$pkg"
            done
        elif have apt; then
            sudo apt-get update -qq
            sudo apt-get install -y ffmpeg python3 python3-venv python3-pip jq
        elif have dnf; then
            sudo dnf install -y ffmpeg python3 python3-pip jq
        else
            echo "[setup] unknown Linux package manager; ensure ffmpeg + python3 + jq are installed manually." >&2
        fi
        BREW_PY="$(command -v python3.11 || command -v python3)"
        ;;
    *)
        echo "[setup] unsupported OS: $OS" >&2
        exit 1
        ;;
esac

# 2) Python venv
if [ ! -d "$ROOT/venv" ]; then
    echo "[setup] creating venv at $ROOT/venv (python: $BREW_PY)"
    "$BREW_PY" -m venv "$ROOT/venv"
fi

echo "[setup] installing Python deps into venv"
"$ROOT/venv/bin/pip" install --quiet --upgrade pip
"$ROOT/venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"

# 3) Permissions
chmod +x "$ROOT/bin/dj-edit" \
         "$ROOT/bin/ingest.sh" \
         "$ROOT/bin/render.sh" \
         "$ROOT/bin/analyze-audio.py" \
         "$ROOT/bin/sync-cameras.py" \
         "$ROOT/bin/pick-hook.py" \
         "$ROOT/bin/score-shots.py" \
         "$ROOT/bin/build-edl.py" 2>/dev/null || true

if [ -f "$ROOT/dj-edit-mac.command" ]; then
    chmod +x "$ROOT/dj-edit-mac.command"
fi

# 4) Verify
echo ""
echo "[setup] verifying installation..."
"$ROOT/bin/dj-edit" help 2>&1 | head -3
echo ""
echo "[setup] ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
echo "[setup] python: $("$ROOT/venv/bin/python" --version)"
echo ""
echo "[setup] DONE. Try: $ROOT/bin/dj-edit quickstart --demo"
