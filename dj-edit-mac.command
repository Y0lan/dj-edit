#!/usr/bin/env bash
# dj-edit-mac.command — double-clickable GUI launcher for macOS.
#
# v0.3 enhancements:
#   - Launch menu: New project / Re-edit recent / Quick test / Doctor / Demo
#   - Style + reference pickers (osascript choose-from-list)
#   - Quick test mode renders single highest-intensity drop in ~3 min
#   - Saved-projects list in ~/.dj-edit-recent (last 5 paths)
#   - Final notification when done
set -uo pipefail

_realpath() {
    python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$1"
}

HERE="$(dirname "$(_realpath "$0")")"
if [[ "$HERE" == "/Applications" ]] || [[ "$HERE" == "/Applications/" ]]; then
    for cand in /usr/local/share/dj-edit /opt/homebrew/share/dj-edit; do
        if [ -x "$cand/bin/dj-edit" ]; then
            HERE="$cand"
            break
        fi
    done
fi

DJ="$HERE/bin/dj-edit"
if [ ! -x "$DJ" ]; then
    osascript -e "display alert \"dj-edit not found\" message \"Could not locate the dj-edit installation. Tried: $HERE/bin/dj-edit\nReinstall with: brew install Y0lan/dj-edit/dj-edit\""
    exit 1
fi

RECENT_FILE="$HOME/.dj-edit-recent"

# ── osascript helpers ──
pick_folder() {
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to POSIX path of (choose folder with prompt "$1")
    return chosen
on error
    return ""
end try
APPLESCRIPT
}

pick_file() {
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to POSIX path of (choose file with prompt "$1" of type $2)
    return chosen
on error
    return ""
end try
APPLESCRIPT
}

pick_text() {
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to text returned of (display dialog "$1" default answer "$2")
    return chosen
on error
    return ""
end try
APPLESCRIPT
}

# Choose from a list. First positional after prompt is the default item.
pick_list() {
    local prompt="$1"; shift
    local items=""
    local default=""
    for it in "$@"; do
        if [ -z "$default" ]; then default="\"$it\""; fi
        if [ -z "$items" ]; then items="\"$it\""; else items="$items, \"$it\""; fi
    done
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to choose from list {$items} with prompt "$prompt" default items {$default}
    if chosen is false then return ""
    return item 1 of chosen as text
on error
    return ""
end try
APPLESCRIPT
}

ask_yn() {
    osascript <<APPLESCRIPT 2>/dev/null
try
    set r to display dialog "$1" buttons {"No", "Yes"} default button "No"
    return button returned of r
on error
    return "No"
end try
APPLESCRIPT
}

# ── Recent projects helpers ──
load_recent() {
    [ -f "$RECENT_FILE" ] && head -5 "$RECENT_FILE"
}

save_recent() {
    local newp="$1"
    local tmp="$RECENT_FILE.tmp.$$"
    {
        echo "$newp"
        [ -f "$RECENT_FILE" ] && grep -vFx -- "$newp" "$RECENT_FILE" | head -4
    } > "$tmp"
    mv "$tmp" "$RECENT_FILE"
}

# ── Mode picker ──
MODE_NEW="Start a new project"
MODE_RECENT="Re-edit a recent project"
MODE_QUICK="Quick test (single drop, ~3 min)"
MODE_DEMO="Run the bundled demo"
MODE_DOCTOR="dj-edit doctor (check install)"

HAS_RECENT="no"
[ -s "$RECENT_FILE" ] && HAS_RECENT="yes"

if [ "$HAS_RECENT" = "yes" ]; then
    MODE=$(pick_list "What do you want to do?" \
        "$MODE_NEW" "$MODE_RECENT" "$MODE_QUICK" "$MODE_DEMO" "$MODE_DOCTOR")
else
    MODE=$(pick_list "What do you want to do?" \
        "$MODE_NEW" "$MODE_QUICK" "$MODE_DEMO" "$MODE_DOCTOR")
fi
[ -z "$MODE" ] && exit 0

# ── Doctor mode ──
if [ "$MODE" = "$MODE_DOCTOR" ]; then
    TERM_SCRIPT=$(mktemp /tmp/dj-edit-doctor-XXXXXX.sh)
    cat > "$TERM_SCRIPT" <<TERMSCRIPT
#!/usr/bin/env bash
echo "=== dj-edit doctor ==="
"$DJ" doctor
echo ""
echo "Press any key to close..."
read -n 1
TERMSCRIPT
    chmod +x "$TERM_SCRIPT"
    open -a Terminal "$TERM_SCRIPT"
    exit 0
fi

# ── Demo mode ──
if [ "$MODE" = "$MODE_DEMO" ]; then
    TERM_SCRIPT=$(mktemp /tmp/dj-edit-demo-XXXXXX.sh)
    cat > "$TERM_SCRIPT" <<TERMSCRIPT
#!/usr/bin/env bash
echo "=== dj-edit quickstart --demo ==="
"$DJ" quickstart --demo
echo ""
echo "Press any key to close..."
read -n 1
TERMSCRIPT
    chmod +x "$TERM_SCRIPT"
    open -a Terminal "$TERM_SCRIPT"
    exit 0
fi

# ── Style + reference pickers ──
STYLE_LABELS=(
    "Signature — balanced default (recommended)"
    "Aggressive — drop_impact every drop, whip-pans dominate"
    "Clean — cinematic, slow reveals, no hard cuts"
)
STYLE_VALUES=("signature" "aggressive" "clean")

REF_LABELS=(
    "None — just use the style"
    "Anyma — melodic techno, long breakdowns"
    "Fisher — tech-house, drop-impact everywhere"
    "Rinse — UK pirate-radio handheld feel"
)
REF_VALUES=("" "anyma" "fisher" "rinse")

pick_style_and_ref() {
    local sel
    sel=$(pick_list "Pick a style:" "${STYLE_LABELS[@]}")
    [ -z "$sel" ] && return 1
    STYLE=""
    for i in "${!STYLE_LABELS[@]}"; do
        if [ "${STYLE_LABELS[$i]}" = "$sel" ]; then
            STYLE="${STYLE_VALUES[$i]}"
            break
        fi
    done

    sel=$(pick_list "Pick an artist reference (optional):" "${REF_LABELS[@]}")
    if [ -z "$sel" ]; then
        REFERENCE=""
        return 0
    fi
    REFERENCE=""
    for i in "${!REF_LABELS[@]}"; do
        if [ "${REF_LABELS[$i]}" = "$sel" ]; then
            REFERENCE="${REF_VALUES[$i]}"
            break
        fi
    done
    return 0
}

# ── Re-edit recent project (skip ingest, just rebuild EDL + render) ──
if [ "$MODE" = "$MODE_RECENT" ]; then
    RECENT_LIST=()
    while IFS= read -r p; do
        [ -n "$p" ] && RECENT_LIST+=("$p")
    done < <(load_recent)
    if [ ${#RECENT_LIST[@]} -eq 0 ]; then
        osascript -e 'display alert "No recent projects" message "Start a new project first."'
        exit 0
    fi
    PROJECT=$(pick_list "Pick a recent project:" "${RECENT_LIST[@]}")
    [ -z "$PROJECT" ] && exit 0
    if [ ! -d "$PROJECT" ]; then
        osascript -e "display alert \"Project missing\" message \"$PROJECT no longer exists on disk.\""
        exit 1
    fi
    STYLE=""; REFERENCE=""
    pick_style_and_ref || exit 0

    EDL_ARGS="--style=$STYLE"
    [ -n "$REFERENCE" ] && EDL_ARGS="$EDL_ARGS --reference=$REFERENCE"
    TERM_SCRIPT=$(mktemp /tmp/dj-edit-restyle-XXXXXX.sh)
    cat > "$TERM_SCRIPT" <<TERMSCRIPT
#!/usr/bin/env bash
set -e
echo "=== Re-editing project: $PROJECT ==="
echo "Style: $STYLE${REFERENCE:+ / Reference: $REFERENCE}"
echo ""
echo "Rebuilding EDL with new style..."
"$DJ" edl "$PROJECT" $EDL_ARGS
echo "Re-rendering..."
"$DJ" deliver "$PROJECT"
echo ""
osascript -e 'display notification "dj-edit re-render done!" with title "dj-edit"'
echo "Press any key to close..."
read -n 1
TERMSCRIPT
    chmod +x "$TERM_SCRIPT"
    save_recent "$PROJECT"
    open -a Terminal "$TERM_SCRIPT"
    exit 0
fi

# ── New project + Quick test ──
IS_QUICK="no"
[ "$MODE" = "$MODE_QUICK" ] && IS_QUICK="yes"

osascript -e 'display dialog "We will ask for:\n  1. Project location\n  2. Master audio (WAV/FLAC)\n  3. Sony A7-III footage (optional)\n  4. Insta360 ERP MP4 (optional)\n  5. DJI Action MP4 (optional)\n  6. Style + reference\n\nClick OK to begin." buttons {"Cancel", "OK"} default button "OK"' >/dev/null 2>&1
[ $? -ne 0 ] && exit 0

# 1. Project location
PROJ_PARENT=$(pick_folder "Choose a folder where your project will be CREATED (e.g. an external drive with lots of space):")
[ -z "$PROJ_PARENT" ] && exit 0

PROJ_NAME=$(pick_text "Project name (no spaces):" "dj-edit-$(date +%Y%m%d-%H%M)")
[ -z "$PROJ_NAME" ] && exit 0

PROJECT="$PROJ_PARENT$PROJ_NAME"

# 2. Master audio
AUDIO=$(pick_file "Choose your master audio file (the clean mixer recording):" '{"wav","flac","mp3","m4a","aiff"}')
[ -z "$AUDIO" ] && exit 0

# 3. Sony A7-III (optional)
A7III=""
if [ "$(ask_yn 'Do you have Sony A7-III footage?')" = "Yes" ]; then
    A7III=$(pick_file "Choose your Sony A7-III footage:" '{"mp4","mov","mts"}')
fi

# 4. Insta360 (optional)
INSTA360=""
if [ "$(ask_yn 'Do you have Insta360 footage? (Must be exported as equirectangular MP4 from Insta360 Studio.)')" = "Yes" ]; then
    INSTA360=$(pick_file "Choose your Insta360 equirectangular MP4:" '{"mp4","mov"}')
fi

# 5. DJI Action (optional)
DJI=""
if [ "$(ask_yn 'Do you have DJI Action footage?')" = "Yes" ]; then
    DJI=$(pick_file "Choose your DJI Action MP4:" '{"mp4","mov"}')
fi

if [ -z "$A7III$INSTA360$DJI" ]; then
    osascript -e 'display alert "No camera selected" message "You need at least one camera source. Re-run and pick one."'
    exit 1
fi

# 6. Style + reference
STYLE=""; REFERENCE=""
pick_style_and_ref || exit 0

# Summary
SUMMARY="Project: $PROJECT\\nAudio:   $AUDIO"
[ -n "$A7III" ]    && SUMMARY="$SUMMARY\\nA7-III:  $A7III"
[ -n "$INSTA360" ] && SUMMARY="$SUMMARY\\nInsta360: $INSTA360"
[ -n "$DJI" ]      && SUMMARY="$SUMMARY\\nDJI:      $DJI"
SUMMARY="$SUMMARY\\nStyle:   $STYLE${REFERENCE:+ / Reference: $REFERENCE}"
if [ "$IS_QUICK" = "yes" ]; then
    SUMMARY="$SUMMARY\\n\\nQuick test mode: renders the single highest-intensity drop only (~3 min)."
else
    SUMMARY="$SUMMARY\\n\\nFull render — 20-50 min depending on set length."
fi
SUMMARY="$SUMMARY\\n\\nOK to proceed?"

osascript -e "display dialog \"$SUMMARY\" buttons {\"Cancel\", \"Render\"} default button \"Render\"" >/dev/null 2>&1
[ $? -ne 0 ] && exit 0

INGEST_ARGS=("--audio=$AUDIO")
[ -n "$A7III" ]    && INGEST_ARGS+=("--a7iii=$A7III")
[ -n "$INSTA360" ] && INGEST_ARGS+=("--insta360=$INSTA360")
[ -n "$DJI" ]      && INGEST_ARGS+=("--dji=$DJI")

EDL_ARGS="--style=$STYLE"
[ -n "$REFERENCE" ] && EDL_ARGS="$EDL_ARGS --reference=$REFERENCE"

TERM_SCRIPT=$(mktemp /tmp/dj-edit-run-XXXXXX.sh)

if [ "$IS_QUICK" = "yes" ]; then
    cat > "$TERM_SCRIPT" <<TERMSCRIPT
#!/usr/bin/env bash
set -e
echo "=== dj-edit — QUICK TEST MODE ==="
echo "Project: $PROJECT"
echo "Style: $STYLE${REFERENCE:+ / Reference: $REFERENCE}"
echo ""
"$DJ" init "$PROJECT"
"$DJ" ingest "$PROJECT" ${INGEST_ARGS[@]}
"$DJ" analyze "$PROJECT"
"$DJ" sync "$PROJECT"
"$DJ" score-360 "$PROJECT" || true
"$DJ" edl "$PROJECT" $EDL_ARGS
# Trim EDL to the highest-intensity drop only
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("$PROJECT")
events = json.loads((p / "events.json").read_text())
drops = sorted([e for e in events["events"] if e["type"] == "drop"],
               key=lambda e: e["intensity"], reverse=True)
if not drops:
    print("WARN: no drops detected; full set will render")
else:
    top = drops[0]
    print(f"Quick mode: rendering only drop at t={top['t']:.1f}s (intensity {top['intensity']:.2f})")
    lo = max(0, top["t"] - 10)
    hi = top["t"] + 8
    for aspect in ("9x16", "16x9"):
        edl_path = p / f"edl_{aspect}.json"
        if not edl_path.exists():
            continue
        edl = json.loads(edl_path.read_text())
        edl["clips"] = [c for c in edl["clips"] if lo <= c.get("master_in", c["in"]) <= hi]
        edl["audio_master_start"] = max(0, lo)
        edl["audio_master_end"] = hi
        offset = edl["clips"][0]["in"] if edl["clips"] else 0
        for c in edl["clips"]:
            c["in"] -= offset
            c["out"] -= offset
        edl_path.write_text(json.dumps(edl, indent=2))
        print(f"  trimmed {aspect}: {len(edl['clips'])} clips covering [{lo:.1f}, {hi:.1f}]")
PY
"$DJ" deliver "$PROJECT"
echo ""
osascript -e 'display notification "Quick test done — check your drop clip!" with title "dj-edit"'
echo ""
echo "If this looks good, re-run dj-edit-mac.command and pick 'Re-edit a recent project'"
echo "to render the FULL set with the same style."
echo ""
echo "Press any key to close..."
read -n 1
TERMSCRIPT
else
    cat > "$TERM_SCRIPT" <<TERMSCRIPT
#!/usr/bin/env bash
set -e
echo "=== dj-edit ==="
echo "Project: $PROJECT"
echo "Style: $STYLE${REFERENCE:+ / Reference: $REFERENCE}"
echo ""
"$DJ" init "$PROJECT"
"$DJ" ingest "$PROJECT" ${INGEST_ARGS[@]}
"$DJ" analyze "$PROJECT"
"$DJ" sync "$PROJECT"
"$DJ" hook "$PROJECT" || true
"$DJ" score "$PROJECT"
"$DJ" score-360 "$PROJECT" || true
"$DJ" edl "$PROJECT" $EDL_ARGS
"$DJ" deliver "$PROJECT"
echo ""
osascript -e 'display notification "dj-edit finished!" with title "dj-edit"'
echo "Press any key to close..."
read -n 1
TERMSCRIPT
fi
chmod +x "$TERM_SCRIPT"

save_recent "$PROJECT"
open -a Terminal "$TERM_SCRIPT"

exit 0
