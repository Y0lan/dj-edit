#!/usr/bin/env bash
# render.sh — turn edl_<aspect>.json into the final mp4 with per-chunk resumable cache.
#
# Renders the EDL in fixed-size chunks (default 30 clips per chunk) to
# cache/chunks/<aspect>_NN_<hash>.mp4. Concat-demuxer stitches cached chunks
# into the final output. If the script is interrupted, only the in-flight
# chunk's work is lost — previously rendered chunks are reused on re-run.
#
# Platform-aware encode: auto-detects videotoolbox (macOS), vaapi (Linux+GPU),
# falls back to libx264.
set -euo pipefail

usage() {
    cat <<EOF
usage: render.sh <project_dir> --aspect=<9x16|16x9|1x1> [--out=<path>]
                              [--encoder=auto|vaapi|videotoolbox|x264] [--qp=22]
                              [--chunk-size=30]

Audio window auto-derived from EDL's audio_master_start/end fields.
Per-chunk cache makes re-runs near-instant if the EDL hasn't changed.
EOF
    exit 1
}

PROJECT=""
ASPECT=""
OUT=""
ENCODER="auto"
QP=22
CHUNK_SIZE=30

[ $# -lt 2 ] && usage
for arg in "$@"; do
    case "$arg" in
        --aspect=*) ASPECT="${arg#*=}" ;;
        --out=*)    OUT="${arg#*=}" ;;
        --encoder=*) ENCODER="${arg#*=}" ;;
        --qp=*)     QP="${arg#*=}" ;;
        --chunk-size=*) CHUNK_SIZE="${arg#*=}" ;;
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
    *) echo "unknown encoder: $ENCODER" >&2; exit 4 ;;
esac
if [ "$ENCODER" = "vaapi" ] && [ ! -e /dev/dri/renderD128 ]; then
    echo "ENCODER=vaapi but /dev/dri/renderD128 not found. Use --encoder=x264 or --encoder=videotoolbox." >&2
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
mkdir -p "$PROJECT/cache/chunks"

# Resolve audio master path
AUDIO_REL=$("$VENV_PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('audio',{}).get('path',''))" "$PROJECT/manifest.json")
[ -z "$AUDIO_REL" ] && { echo "ERROR: manifest.json has no audio.path — re-run dj-edit ingest" >&2; exit 3; }
AUDIO="$PROJECT/$AUDIO_REL"
[ -f "$AUDIO" ] || { echo "ERROR: audio missing: $AUDIO" >&2; exit 3; }

# ── Two-pass loudnorm: pass 1 measures, pass 2 applies ──
AUDIO_HASH=$("$VENV_PY" -c "import os,sys;st=os.stat(sys.argv[1]);print(f'{st.st_size}_{int(st.st_mtime)}')" "$AUDIO")
LOUDNORM_JSON="$PROJECT/cache/loudnorm_$(basename "$AUDIO_REL")_${AUDIO_HASH}.json"
if [ ! -s "$LOUDNORM_JSON" ]; then
    echo "[loudnorm] pass 1: measuring $AUDIO..." >&2
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
    echo "WARN: single-pass loudnorm fallback" >&2
    AFILTER="loudnorm=I=-14:TP=-1:LRA=11"
fi

# ── Plan chunks: split EDL into chunks of CHUNK_SIZE clips each ──
CHUNK_PLAN=$(mktemp /tmp/dj-edit-chunk-plan-XXXXXX.json)
CONCAT_LIST=$(mktemp /tmp/dj-edit-concat-XXXXXX.txt)
trap "rm -f $CHUNK_PLAN $CONCAT_LIST" EXIT

DJ_EDIT_ROOT="$DJ_EDIT_ROOT" "$VENV_PY" - "$PROJECT" "$EDL" "$ASPECT" "$CHUNK_SIZE" "$CHUNK_PLAN" <<'PY'
import hashlib, json, sys
from pathlib import Path

proj = Path(sys.argv[1])
edl_path = Path(sys.argv[2])
aspect = sys.argv[3]
chunk_size = int(sys.argv[4])
plan_out = Path(sys.argv[5])

edl = json.loads(edl_path.read_text())
clips = edl["clips"]

chunks = []
for i in range(0, len(clips), chunk_size):
    sub = clips[i:i + chunk_size]
    # Stable hash of the chunk's EDL slice
    blob = json.dumps(sub, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(blob.encode()).hexdigest()[:16]
    cache_path = proj / "cache" / "chunks" / f"{aspect}_chunk{i//chunk_size:04d}_{h}.mp4"
    chunks.append({
        "index": i // chunk_size,
        "start_clip": i,
        "end_clip": min(i + chunk_size, len(clips)),
        "hash": h,
        "cache_path": str(cache_path),
        "clips": sub,
    })

plan_out.write_text(json.dumps(chunks))
print(f"[chunks] {len(chunks)} chunks ({chunk_size} clips/chunk)", file=sys.stderr)
PY

N_CHUNKS=$("$VENV_PY" -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$CHUNK_PLAN")
echo "[render] $OUT (${OUT_W}x${OUT_H}@${TARGET_FPS}, encoder=$ENCODER, $N_CHUNKS chunks)" >&2

# ── Render each chunk that's not already cached ──
CHUNK_GRAPH=$(mktemp /tmp/dj-edit-chunk-graph-XXXXXX.txt)
CHUNK_INPUTS=$(mktemp /tmp/dj-edit-chunk-inputs-XXXXXX.txt)
trap "rm -f $CHUNK_PLAN $CONCAT_LIST $CHUNK_GRAPH $CHUNK_INPUTS" EXIT

RENDERED=0
CACHED=0
for i in $(seq 0 $((N_CHUNKS - 1))); do
    CACHE_PATH=$("$VENV_PY" -c "import json,sys;p=json.load(open(sys.argv[1]));print(p[$i]['cache_path'])" "$CHUNK_PLAN")
    if [ -s "$CACHE_PATH" ]; then
        echo "  chunk $((i+1))/$N_CHUNKS: CACHED ($(basename "$CACHE_PATH"))" >&2
        CACHED=$((CACHED + 1))
        continue
    fi

    # Build per-chunk filter_complex + input args
    DJ_EDIT_ROOT="$DJ_EDIT_ROOT" "$VENV_PY" - "$PROJECT" "$CHUNK_PLAN" "$i" "$OUT_W" "$OUT_H" "$TARGET_FPS" "$CHUNK_GRAPH" "$CHUNK_INPUTS" <<'PY'
import json, os, sys
from pathlib import Path

proj = Path(sys.argv[1])
plan_path = Path(sys.argv[2])
chunk_idx = int(sys.argv[3])
out_w, out_h, fps = int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
graph_out = Path(sys.argv[7])
args_out = Path(sys.argv[8])

dj_root = Path(os.environ["DJ_EDIT_ROOT"]).resolve()
sys.path.insert(0, str(dj_root))
from lib.selector import build_crop_filter
from lib.treatments import flash_freeze
from lib.moves import filter_chain as v360_filter_chain

plan = json.loads(plan_path.read_text())
chunk = plan[chunk_idx]
clips = chunk["clips"]

# Per-chunk EDL needs source dimensions — re-load from EDL meta
edl_path = proj / list(proj.glob("edl_*.json"))[0].name  # any aspect
edl = json.loads(edl_path.read_text())
src_w = edl.get("src_w", 3840)
src_h = edl.get("src_h", 2160)


def escape_ffmpeg_path(p):
    out = []
    for ch in p:
        if ch in (':', '\\', ',', ';', "'", '%'):
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


def escape_drawtext_text(s):
    s = s.replace('\n', ' ').replace('\r', ' ')
    out = []
    for ch in s:
        if ch in ("'", ':', '\\', ',', '%'):
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


src_paths = {}
def src_idx(rel):
    if rel not in src_paths:
        src_paths[rel] = len(src_paths)
    return src_paths[rel]

graph_lines = []
out_labels = []
for i, clip in enumerate(clips):
    si = src_idx(clip["src"])
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

    label = f"v{i}"
    graph_lines.append(",".join(chain) + f"[{label}];")
    out_labels.append(f"[{label}]")

graph_lines.append("".join(out_labels) + f"concat=n={len(clips)}:v=1:a=0[vout]")
graph_out.write_text("\n".join(graph_lines))

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
PY

    # Read input args
    CHUNK_INPUT_ARGS=()
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$line" ] && CHUNK_INPUT_ARGS+=("$line")
    done < "$CHUNK_INPUTS"
    if [ ${#CHUNK_INPUT_ARGS[@]} -eq 0 ]; then
        echo "ERROR: chunk $i has no input sources" >&2
        exit 6
    fi

    # Encoder-specific final graph + args
    if [ "$ENCODER" = "vaapi" ]; then
        HW_INIT=(-init_hw_device "vaapi=va:/dev/dri/renderD128" -filter_hw_device va)
        CHUNK_GRAPH_FINAL=$(mktemp /tmp/dj-edit-chunk-final-XXXXXX.txt)
        { cat "$CHUNK_GRAPH"; echo "; [vout]hwupload[vhw]"; } > "$CHUNK_GRAPH_FINAL"
        ENCODE_ARGS=(-map "[vhw]" -c:v h264_vaapi -qp:v "$QP" -profile:v main)
    elif [ "$ENCODER" = "videotoolbox" ]; then
        HW_INIT=()
        CHUNK_GRAPH_FINAL="$CHUNK_GRAPH"
        ENCODE_ARGS=(-map "[vout]" -c:v h264_videotoolbox -b:v 12M -profile:v high)
    else
        HW_INIT=()
        CHUNK_GRAPH_FINAL="$CHUNK_GRAPH"
        ENCODE_ARGS=(-map "[vout]" -c:v libx264 -preset medium -crf "$QP")
    fi

    CHUNK_PARTIAL="${CACHE_PATH}.partial.mp4"
    rm -f "$CHUNK_PARTIAL"

    START_T=$(date +%s)
    if ! ffmpeg -y -hide_banner -loglevel error -nostats \
        "${HW_INIT[@]}" \
        "${CHUNK_INPUT_ARGS[@]}" \
        -/filter_complex "$CHUNK_GRAPH_FINAL" \
        "${ENCODE_ARGS[@]}" -pix_fmt yuv420p -an \
        "$CHUNK_PARTIAL" 2>&1; then
        echo "ERROR: chunk $i render failed; partial at $CHUNK_PARTIAL" >&2
        [ "$ENCODER" = "vaapi" ] && rm -f "$CHUNK_GRAPH_FINAL"
        exit 7
    fi
    [ "$ENCODER" = "vaapi" ] && rm -f "$CHUNK_GRAPH_FINAL"

    # Atomic move into cache
    mv "$CHUNK_PARTIAL" "$CACHE_PATH"
    ELAPSED=$(( $(date +%s) - START_T ))
    echo "  chunk $((i+1))/$N_CHUNKS: rendered ($(basename "$CACHE_PATH"), ${ELAPSED}s)" >&2
    RENDERED=$((RENDERED + 1))
done

echo "[render] chunks: $CACHED cached, $RENDERED freshly rendered" >&2

# ── Build concat list ──
"$VENV_PY" -c "
import json,sys
plan = json.load(open(sys.argv[1]))
out = sys.argv[2]
with open(out, 'w') as f:
    for c in plan:
        p = c['cache_path'].replace(\"'\", \"'\\\\''\")
        f.write(f\"file '{p}'\n\")
" "$CHUNK_PLAN" "$CONCAT_LIST"

# ── Final mux: concat (stream copy video) + audio loudnorm + AAC ──
A_PRE=()
A_POST=()
if [ -n "$AUDIO_START" ] && [ "$AUDIO_START" != "0.0" ] && [ "$AUDIO_START" != "0" ]; then
    A_PRE+=(-ss "$AUDIO_START")
fi
if [ -n "$AUDIO_END" ] && [ "$AUDIO_END" != "0.0" ] && [ "$AUDIO_END" != "0" ]; then
    A_POST+=(-to "$AUDIO_END")
fi

OUT_PARTIAL="${OUT}.partial.mp4"
rm -f "$OUT_PARTIAL"

echo "[mux] concatenating chunks + applying loudnorm to audio..." >&2
ffmpeg -y -hide_banner -stats \
    -f concat -safe 0 -i "$CONCAT_LIST" \
    "${A_PRE[@]}" -i "$AUDIO" "${A_POST[@]}" \
    -map 0:v:0 -c:v copy \
    -map 1:a:0 -af "$AFILTER,aresample=48000:async=1" -c:a aac -b:a 320k \
    -movflags +faststart \
    "$OUT_PARTIAL"

mv "$OUT_PARTIAL" "$OUT"

echo "[render] done: $OUT" >&2
ls -lh "$OUT" >&2
