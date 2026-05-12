#!/usr/bin/env bash
# ingest.sh — copy footage into project, build manifest, validate ERP.
set -euo pipefail

usage() {
    cat <<EOF
usage: ingest.sh <project_dir> [--insta360=PATH]... [--a7iii=PATH]... [--dji=PATH]... --audio=PATH
                              [--insta360-projection=e|dfisheye]

PATH may be a file or directory. Repeat per-camera flags to ingest multiple sources.
ERP validation rejects Insta360 files where |aspect - 2.0| > 0.05.
EOF
    exit 1
}

[ $# -lt 2 ] && usage

PROJECT=""
INSTA360_PATHS=()
A7III_PATHS=()
DJI_PATHS=()
AUDIO_PATH=""
INSTA360_PROJ="e"

for arg in "$@"; do
    case "$arg" in
        --insta360=*) INSTA360_PATHS+=("${arg#*=}") ;;
        --a7iii=*)    A7III_PATHS+=("${arg#*=}") ;;
        --dji=*)      DJI_PATHS+=("${arg#*=}") ;;
        --audio=*)    AUDIO_PATH="${arg#*=}" ;;
        --insta360-projection=*) INSTA360_PROJ="${arg#*=}" ;;
        --*) echo "unknown flag: $arg" >&2; usage ;;
        *)   [ -z "$PROJECT" ] && PROJECT="$arg" ;;
    esac
done

[ -z "$PROJECT" ] && { echo "missing <project_dir>" >&2; usage; }
[ -z "$AUDIO_PATH" ] && { echo "--audio is required" >&2; usage; }
[ -f "$AUDIO_PATH" ] || { echo "audio file not found: $AUDIO_PATH" >&2; exit 2; }

mkdir -p "$PROJECT"/footage/{insta360,a7iii,dji,audio}
mkdir -p "$PROJECT"/cmds "$PROJECT"/cache "$PROJECT"/out/drops "$PROJECT"/out/thumbs

# Portable realpath (BSD readlink lacks -f)
_realpath() {
    python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$1"
}

link_into() {
    local dest="$1"; shift
    for src in "$@"; do
        [ -n "$src" ] || continue
        [ -e "$src" ] || { echo "warning: $src missing — skipping" >&2; continue; }
        if [ -d "$src" ]; then
            for f in "$src"/*; do
                [ -f "$f" ] && ln -sfn "$(_realpath "$f")" "$dest/$(basename "$f")"
            done
        else
            ln -sfn "$(_realpath "$src")" "$dest/$(basename "$src")"
        fi
    done
}

if [ ${#INSTA360_PATHS[@]} -gt 0 ]; then link_into "$PROJECT/footage/insta360" "${INSTA360_PATHS[@]}"; fi
if [ ${#A7III_PATHS[@]} -gt 0 ];    then link_into "$PROJECT/footage/a7iii"    "${A7III_PATHS[@]}"; fi
if [ ${#DJI_PATHS[@]} -gt 0 ];      then link_into "$PROJECT/footage/dji"      "${DJI_PATHS[@]}"; fi

# Audio: only link if source != destination (handles already-inside-project case)
AUDIO_DEST="$PROJECT/footage/audio/$(basename "$AUDIO_PATH")"
AUDIO_ABS=$(_realpath "$AUDIO_PATH")
DEST_ABS=$(_realpath "$AUDIO_DEST" 2>/dev/null || echo "")
if [ "$AUDIO_ABS" != "$DEST_ABS" ]; then
    ln -sfn "$AUDIO_ABS" "$AUDIO_DEST"
fi

python3 - "$PROJECT" "$INSTA360_PROJ" "$(basename "$AUDIO_PATH")" <<'PY'
import json, subprocess, sys
from pathlib import Path

proj = Path(sys.argv[1])
ins_proj = sys.argv[2]

def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None

def vinfo(p):
    d = probe(p)
    if not d: return None
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v: return None
    num, den = (v.get("avg_frame_rate", "0/1").split("/") + ["1"])[:2]
    fps = float(num)/float(den) if float(den) else 0.0
    w, h = v.get("width", 0), v.get("height", 0)
    return {
        "path": str(p.relative_to(proj)),
        "width": w, "height": h, "fps": round(fps, 3),
        "duration": round(float(d.get("format", {}).get("duration", 0)), 3),
        "aspect": round((w/h) if h else 0, 4),
        "codec": v.get("codec_name", ""),
    }

def ainfo(p):
    d = probe(p)
    if not d: return None
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not a: return None
    return {
        "path": str(p.relative_to(proj)),
        "duration": round(float(d.get("format", {}).get("duration", 0)), 3),
        "sample_rate": int(a.get("sample_rate", 0)),
        "channels": a.get("channels", 0),
        "codec": a.get("codec_name", ""),
    }

footage = proj / "footage"
manifest = {"insta360_projection": ins_proj, "cameras": {}, "audio": None, "target_fps": 30}

# .insv must be in scan set so the rejection branch fires; other extensions are valid.
valid_exts = (".mp4", ".mov", ".mkv", ".m4v", ".insv")
for cam in ("insta360", "a7iii", "dji"):
    cam_dir = footage / cam
    files = sorted([p for p in cam_dir.iterdir() if p.is_file() and p.suffix.lower() in valid_exts])
    manifest["cameras"][cam] = []
    for f in files:
        if f.suffix.lower() == ".insv":
            print(f"ERROR: {f.name} is a raw .insv (dual-fisheye). Open Insta360 Studio, "
                  f"export as equirectangular MP4, and re-run ingest with the .mp4 path.",
                  flush=True)
            sys.exit(3)
        info = vinfo(f)
        if not info:
            print(f"WARN: could not probe {f}", flush=True)
            continue
        if cam == "insta360" and ins_proj == "e":
            if abs(info["aspect"] - 2.0) > 0.05:
                print(f"ERROR: {f.name} aspect {info['aspect']} suggests this is NOT a 360 "
                      f"equirectangular export. dj-edit needs the raw 360 footage to do "
                      f"virtual camera moves. Either re-export from Insta360 Studio with "
                      f"'Export 360° equirectangular' (2:1 aspect), or if this is a flat "
                      f"reframed export, ingest it as --a7iii=... instead. For dual-fisheye, "
                      f"pass --insta360-projection=dfisheye.", flush=True)
                sys.exit(3)
        manifest["cameras"][cam].append(info)

# Use the audio file we explicitly received, not whatever's in footage/audio/.
# The wrapper passes AUDIO_DEST as the 3rd argv.
audio_dest_basename = sys.argv[3] if len(sys.argv) >= 4 and sys.argv[3] else None
if audio_dest_basename:
    audio_path = footage / "audio" / audio_dest_basename
    if audio_path.exists():
        manifest["audio"] = ainfo(audio_path)
    else:
        # Fallback: first file in the dir
        audio_files = sorted([p for p in (footage / "audio").iterdir() if p.is_file()])
        if audio_files:
            manifest["audio"] = ainfo(audio_files[0])
else:
    audio_files = sorted([p for p in (footage / "audio").iterdir() if p.is_file()])
    if audio_files:
        manifest["audio"] = ainfo(audio_files[0])

all_fps = [c["fps"] for cam_list in manifest["cameras"].values() for c in cam_list if c["fps"] > 0]
if all_fps:
    # Match the slowest source to avoid frame-rate conversion artifacts.
    # Round to nearest integer to keep ffmpeg happy with concat.
    manifest["target_fps"] = int(round(min(all_fps)))

with open(proj / "manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)

n = sum(len(v) for v in manifest["cameras"].values())
print(f"ingested {n} video files | audio={'yes' if manifest['audio'] else 'no'} | target_fps={manifest['target_fps']}")
PY

echo "manifest: $PROJECT/manifest.json"
