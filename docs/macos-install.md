# macOS install guide

Two paths: **Homebrew tap (recommended)** or **source install**.

## Homebrew tap (one command)

```bash
brew tap Y0lan/dj-edit
brew install dj-edit
```

That's it. Brew handles all dependencies: ffmpeg, Python 3.11, the venv, librosa, scipy, numpy, soundfile.

Once installed, three ways to run:

**1. GUI (recommended for non-CLI users)**

Double-click `/Applications/dj-edit-mac.command` in Finder.

First launch only: macOS Gatekeeper will warn about "unidentified developer". Right-click the file → **Open** → confirm. After that, double-click works normally.

**2. Terminal one-shot**

```bash
dj-edit init my-set
dj-edit ingest my-set --a7iii=/path/to/C0004.MP4 --audio=/path/to/master.wav
dj-edit run my-set
```

**3. Quickstart smoke-test (no real footage)**

```bash
dj-edit quickstart --demo
```

Synthesizes a 30s test track + matching video, runs the full pipeline, opens the resulting drop clip in QuickTime. Confirms the install works end-to-end.

## Source install (for development)

```bash
git clone https://github.com/Y0lan/dj-edit.git
cd dj-edit
./setup.sh
```

`setup.sh` runs `brew install ffmpeg python@3.11`, creates `venv/`, installs Python deps from `requirements.txt`.

Then run via the local bin:

```bash
./bin/dj-edit init my-set
./bin/dj-edit run my-set
```

## What gets installed

- `/usr/local/bin/dj-edit` — main CLI dispatcher (or `/opt/homebrew/bin/dj-edit` on Apple Silicon)
- `/Applications/dj-edit-mac.command` — double-clickable GUI launcher
- `/usr/local/share/dj-edit/` — bin/lib/presets contents
- `/usr/local/share/dj-edit/venv/` — Python venv with librosa + scipy + numpy + soundfile

## Uninstall

```bash
brew uninstall dj-edit
brew untap Y0lan/dj-edit
```

## Updating

```bash
brew upgrade dj-edit
```

## Hardware encoding

By default `dj-edit` auto-detects:
- Apple Silicon Mac → `h264_videotoolbox` (hardware encode, fast)
- Intel Mac → `h264_videotoolbox` (hardware encode, fast)
- Override: `dj-edit render my-set --aspect=9x16 --encoder=x264` (slower, software)

A 90-minute set @ 1080p25 renders in ~15-20 min on M-series Mac, ~25-35 min on Intel Mac.
