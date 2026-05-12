#!/usr/bin/env bash
# render.sh — turn edl_<aspect>.json into the final mp4 via ffmpeg filter_complex.
#
# Uses one big filter_complex graph per aspect (no concat demuxer — avoids CFR/VFR
# pitfalls). Platform-aware encode: auto-detects VideoToolbox on macOS, VAAPI on
# Linux with /dev/dri/renderD128, falls back to libx264 elsewhere.
set -euo pipefail

usage() {
    cat <<EOF
usage: render.sh <project_dir> --aspect=<9x16|16x9|1x1> [--out=<path>]
                              [--encoder=auto|vaapi|videotoolbox|x264] [--qp=22]

Audio window is auto-derived from the EDL's audio_master_start/end fields
(set by build-edl.py to match the covered video timeline).
EOF
    exit 1
}

PROJECT=""
ASPECT=""
OUT=""
ENCODER="auto"
QP=22

[ $# -lt 2 ] && usage
for arg in "$@"; do
    case "$arg" in
        --aspect=*) ASPECT="${arg#*=}" ;;
        --out=*)    OUT="${arg#*=}" ;;
        --encoder=*) ENCODER="${arg#*=}" ;;
        --qp=*)     QP="${arg#*=}" ;;
        --*) echo "unknown flag $arg" >&2; usage ;;
        *) [ -z "$PROJECT" ] && PROJECT="$arg" ;;
    esac
done
[ -z "$PROJECT" ] && usage
[ -z "$ASPECT" ] && usage
EDL="$PROJECT/edl_${ASPECT}.json"
[ -f "$EDL" ] || { echo "no EDL at $EDL — run dj-edit edl first" >&2; exit 2; }

# Portable realpath (BSD readlink lacks -f)
_realpath() {
    python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$1"
}

DJ_EDIT_ROOT="$(dirname "$(_realpath "$0")")/.."
DJ_EDIT_ROOT="$(_realpath "$DJ_EDIT_ROOT")"
export DJ_EDIT_ROOT
VENV_PY="$DJ_EDIT_ROOT/venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="python3"

# Encoder auto-detect
if [ "$ENCODER" = "auto" ]; then
    case "$(uname -s)" in
        Darwin) ENCODER="videotoolbox" ;;
        Linux)  [ -e /dev/dri/renderD128 ] && ENCODER="vaapi" || ENCODER="x264" ;;
        *)      ENCODER="x264" ;;
    esac
fi
case "$ENCODER" in
    vaapi|videotoolbox|x264) ;;
    *) echo "unknown encoder: $ENCODER (use auto|vaapi|videotoolbox|x264)" >&2; exit 4 ;;
esac
if [ "$ENCODER" = "vaapi" ] && [ ! -e /dev/dri/renderD128 ]; then
    echo "ENCODER=vaapi but /dev/dri/renderD128 not found. Re-run with --encoder=x264 or --encoder=videotoolbox (Mac)." >&2
    exit 4
fi

case "$ASPECT" in
    9x16) OUT_W=1080; OUT_H=1920 ;;
    16x9) OUT_W=1920; OUT_H=1080 ;;
    1x1)  OUT_W=1080; OUT_H=1080 ;;
    *) echo "bad aspect $ASPECT" >&2; exit 1 ;;
esac

# Read scalar fields from EDL via python (portable, no jq dependency)
_read_edl_field() {
    "$VENV_PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get(sys.argv[2],''))" "$EDL" "$1"
}
TARGET_FPS=$(_read_edl_field target_fps)
[ -z "$TARGET_FPS" ] && TARGET_FPS=30
AUDIO_START=$(_read_edl_field audio_master_start)
AUDIO_END=$(_read_edl_field audio_master_end)
N_CLIPS=$("$VENV_PY" -c "import json,sys;print(len(json.load(open(sys.argv[1])).get('clips',[])))" "$EDL")
if [ "$N_CLIPS" = "0" ]; then
    echo "ERROR: $EDL contains zero clips — re-run dj-edit analyze + dj-edit edl" >&2
    exit 5
fi

[ -z "$OUT" ] && OUT="$PROJECT/out/set_${ASPECT}_${TARGET_FPS}fps.mp4"
mkdir -p "$(dirname "$OUT")"

# ── Output cache: skip the render entirely if the EDL hasn't changed ──
# Hash the EDL contents. If a previous render with the same EDL is in cache,
# copy it to OUT instead of re-rendering. Saves the full 25-40 min on re-runs.
EDL_HASH=$("$VENV_PY" -c "
import hashlib, json, sys
edl = json.load(open(sys.argv[1]))
blob = json.dumps(edl, sort_keys=True, separators=(',', ':'))
print(hashlib.sha256(blob.encode()).hexdigest()[:16])
" "$EDL")
CACHE_OUT="$PROJECT/cache/render_${ASPECT}_${EDL_HASH}.mp4"
mkdir -p "$(dirname "$CACHE_OUT")"
if [ -s "$CACHE_OUT" ]; then
    echo "[render] cache hit ($EDL_HASH) — copying to $OUT" >&2
    cp "$CACHE_OUT" "$OUT"
    ls -lh "$OUT" >&2
    exit 0
fi

# Resolve audio master path (read from manifest via python)
AUDIO_REL=$("$VENV_PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('audio',{}).get('path',''))" "$PROJECT/manifest.json")
[ -z "$AUDIO_REL" ] && { echo "ERROR: manifest.json has no audio.path — re-run dj-edit ingest" >&2; exit 3; }
AUDIO="$PROJECT/$AUDIO_REL"
[ -f "$AUDIO" ] || { echo "ERROR: audio missing: $AUDIO" >&2; exit 3; }

# ── Two-pass loudnorm: pass 1 measures, pass 2 applies ──
# Cache key includes audio file size + mtime so re-encoded audio invalidates cache.
AUDIO_HASH=$("$VENV_PY" -c "import os,sys;st=os.stat(sys.argv[1]);print(f'{st.st_size}_{int(st.st_mtime)}')" "$AUDIO")
LOUDNORM_JSON="$PROJECT/cache/loudnorm_$(basename "$AUDIO_REL")_${AUDIO_HASH}.json"
mkdir -p "$(dirname "$LOUDNORM_JSON")"
if [ ! -s "$LOUDNORM_JSON" ]; then
    echo "[loudnorm pass 1] measuring $AUDIO..." >&2
    LN_RAW=$(mktemp /tmp/dj-edit-ln-raw-XXXXXX.txt)
    ffmpeg -hide_banner -nostats -v info -i "$AUDIO" \
        -af "loudnorm=I=-14:TP=-1:LRA=11:print_format=json" \
        -f null - 2> "$LN_RAW" || true
    "$VENV_PY" -c "
import re, sys, json
text = open(sys.argv[1]).read()
m = re.search(r'\{[^{}]*\"input_i\"[^{}]*\}', text, re.DOTALL)
if m:
    json.dump(json.loads(m.group(0)), sys.stdout)
" "$LN_RAW" > "$LOUDNORM_JSON" || echo "{}" > "$LOUDNORM_JSON"
    rm -f "$LN_RAW"
fi

if [ -s "$LOUDNORM_JSON" ] && [ "$(cat "$LOUDNORM_JSON")" != "{}" ]; then
    eval "$("$VENV_PY" -c "
import json,sys
d = json.load(open(sys.argv[1]))
for k,v in d.items():
    print(f'LN_{k.upper()}=\"{v}\"')
" "$LOUDNORM_JSON")"
    AFILTER="loudnorm=I=-14:TP=-1:LRA=11:measured_I=${LN_INPUT_I}:measured_TP=${LN_INPUT_TP}:measured_LRA=${LN_INPUT_LRA}:measured_thresh=${LN_INPUT_THRESH}:offset=${LN_TARGET_OFFSET}:linear=true"
else
    echo "WARN: single-pass loudnorm fallback (measurement parse failed)" >&2
    AFILTER="loudnorm=I=-14:TP=-1:LRA=11"
fi

# ── Build the filter_complex graph and ffmpeg input args from the EDL ──
GRAPH_TMP=$(mktemp /tmp/dj-edit-graph-XXXXXX.txt)
ARGS_TMP=$(mktemp /tmp/dj-edit-args-XXXXXX.txt)
trap "rm -f $GRAPH_TMP $ARGS_TMP" EXIT

DJ_EDIT_ROOT="$DJ_EDIT_ROOT" "$VENV_PY" - "$PROJECT" "$EDL" "$OUT_W" "$OUT_H" "$TARGET_FPS" "$GRAPH_TMP" "$ARGS_TMP" <<'PY'
import json, os, sys
from pathlib import Path

proj = Path(sys.argv[1])
edl_path = Path(sys.argv[2])
out_w, out_h, fps = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
graph_out = Path(sys.argv[6])
args_out = Path(sys.argv[7])

dj_root = Path(os.environ["DJ_EDIT_ROOT"]).resolve()
sys.path.insert(0, str(dj_root))
from lib.selector import build_crop_filter
from lib.treatments import flash_freeze
from lib.moves import filter_chain as v360_filter_chain

edl = json.loads(edl_path.read_text())
src_w = edl.get("src_w", 3840)
src_h = edl.get("src_h", 2160)
clips = edl["clips"]


def escape_ffmpeg_path(p: str) -> str:
    """Escape a path for use inside an ffmpeg filter (sendcmd=f=..., etc).

    ffmpeg filter-arg metacharacters: : \\ , ;
    Single backslash-escape each, including spaces which ffmpeg tolerates but
    some configurations don't.
    """
    out = []
    for ch in p:
        if ch in (':', '\\', ',', ';', "'", '%'):
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


def escape_drawtext_text(s: str) -> str:
    """Escape text for drawtext=text='...' filter arg.

    Strip control chars; backslash-escape quote, colon, backslash, comma, percent.
    """
    s = s.replace('\n', ' ').replace('\r', ' ')
    out = []
    for ch in s:
        if ch in ("'", ':', '\\', ',', '%'):
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


# Map each unique source file to a single ffmpeg input
src_paths: dict[str, int] = {}
def src_idx(rel: str) -> int:
    if rel not in src_paths:
        src_paths[rel] = len(src_paths)
    return src_paths[rel]

graph_lines = []
out_labels = []
for i, clip in enumerate(clips):
    si = src_idx(clip["src"])
    label = f"v{i}"
    duration = clip["out"] - clip["in"]
    src_in = float(clip["src_in"])
    framing = clip.get("framing", "medium")

    if clip.get("camera") == "insta360":
        move = clip.get("move", "orbit")
        cmd_path = str(dj_root / "presets" / "moves" / f"{move.replace('_', '-')}.cmd")
        v360_chain = v360_filter_chain(escape_ffmpeg_path(cmd_path), out_w, out_h)
        chain = [
            f"[{si}:v]trim=start={src_in}:duration={duration},setpts=PTS-STARTPTS",
            v360_chain,
            f"fps={fps},setsar=1,format=yuv420p",
        ]
    else:
        crop = build_crop_filter(framing, src_w, src_h, out_w, out_h)
        chain = [
            f"[{si}:v]trim=start={src_in}:duration={duration},setpts=PTS-STARTPTS",
            crop,
            f"fps={fps},setsar=1,format=yuv420p",
        ]

    if clip.get("treatment") == "flash_freeze":
        chain.append(flash_freeze(out_w, out_h, fps))

    if clip.get("title"):
        title = escape_drawtext_text(clip["title"])
        chain.append(
            f"drawtext=text='{title}':fontcolor=white:fontsize={out_h // 22}:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:borderw=4:bordercolor=black@0.7"
        )

    chain_str = ",".join(chain)
    graph_lines.append(f"{chain_str}[{label}];")
    out_labels.append(f"[{label}]")

if not clips:
    print("ERROR: zero clips in EDL", file=sys.stderr)
    sys.exit(5)

graph_lines.append("".join(out_labels) + f"concat=n={len(clips)}:v=1:a=0[vout]")

graph_out.write_text("\n".join(graph_lines))

# Write input args: one -i per unique source
input_args = []
for path, _i in sorted(src_paths.items(), key=lambda x: x[1]):
    full = (proj / path).resolve()
    if not full.exists():
        for cam in ("a7iii", "dji", "insta360"):
            cand = proj / "footage" / cam / Path(path).name
            if cand.exists():
                full = cand
                break
    input_args.extend(["-i", str(full)])
args_out.write_text("\n".join(input_args))
print(f"  graph: {len(clips)} clips, {len(src_paths)} unique source files, "
      f"{sum(len(l) for l in graph_lines)} bytes", file=sys.stderr)
PY

# Read back the graph and inputs (portable: while-read instead of mapfile for bash 3.2).
# `|| [ -n "$line" ]` ensures the LAST line (no trailing newline) is also read.
INPUT_ARGS=()
while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] && INPUT_ARGS+=("$line")
done < "$ARGS_TMP"
if [ ${#INPUT_ARGS[@]} -eq 0 ]; then
    echo "ERROR: no video inputs resolved from EDL" >&2
    exit 6
fi

# Audio trim: use EDL's master window (set by build-edl.py).
# Declare as arrays so empty expansion produces zero args (not one empty arg).
A_PRE=()
A_POST=()
if [ -n "$AUDIO_START" ] && [ "$AUDIO_START" != "0.0" ] && [ "$AUDIO_START" != "0" ]; then
    A_PRE+=(-ss "$AUDIO_START")
fi
if [ -n "$AUDIO_END" ] && [ "$AUDIO_END" != "0.0" ] && [ "$AUDIO_END" != "0" ]; then
    A_POST+=(-to "$AUDIO_END")
fi

echo "[render] $OUT (${OUT_W}x${OUT_H}@${TARGET_FPS}, encoder=$ENCODER, audio=[${AUDIO_START:-0}, ${AUDIO_END:-end}])" >&2

OUT_PARTIAL="${OUT}.partial.mp4"

if [ "$ENCODER" = "vaapi" ]; then
    HW_INIT=(-init_hw_device "vaapi=va:/dev/dri/renderD128" -filter_hw_device va)
    GRAPH_TMP_FINAL=$(mktemp /tmp/dj-edit-graph-final-XXXXXX.txt)
    trap "rm -f $GRAPH_TMP $ARGS_TMP $GRAPH_TMP_FINAL" EXIT
    { cat "$GRAPH_TMP"; echo "; [vout]hwupload[vhw]"; } > "$GRAPH_TMP_FINAL"
    ENCODE_ARGS=(-map "[vhw]" -c:v h264_vaapi -qp:v "$QP" -profile:v main)
elif [ "$ENCODER" = "videotoolbox" ]; then
    HW_INIT=()
    GRAPH_TMP_FINAL="$GRAPH_TMP"
    ENCODE_ARGS=(-map "[vout]" -c:v h264_videotoolbox -b:v 12M -profile:v high)
else
    HW_INIT=()
    GRAPH_TMP_FINAL="$GRAPH_TMP"
    ENCODE_ARGS=(-map "[vout]" -c:v libx264 -preset medium -crf "$QP")
fi

A_CHAIN="${AFILTER},aresample=48000:async=1"

# Use -/filter_complex <file> (new syntax) to avoid ARG_MAX issues with large EDLs.
# (The old -filter_complex_script is deprecated in ffmpeg 8+.)
# Empty array expansion `"${arr[@]}"` produces zero args; safe to interpolate.
ffmpeg -y -hide_banner -stats \
    "${HW_INIT[@]}" \
    "${INPUT_ARGS[@]}" \
    "${A_PRE[@]}" -i "$AUDIO" "${A_POST[@]}" \
    -/filter_complex "$GRAPH_TMP_FINAL" \
    "${ENCODE_ARGS[@]}" -pix_fmt yuv420p \
    -map "$((${#INPUT_ARGS[@]} / 2)):a" -af "$A_CHAIN" -c:a aac -b:a 320k \
    -movflags +faststart \
    "$OUT_PARTIAL"

# Atomic rename only on success
mv "$OUT_PARTIAL" "$OUT"

# Populate output cache so future runs with the same EDL are instant
cp "$OUT" "$CACHE_OUT"

echo "[render] done: $OUT (cached at $CACHE_OUT)" >&2
ls -lh "$OUT" >&2
