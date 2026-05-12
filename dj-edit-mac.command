#!/usr/bin/env bash
# dj-edit-mac.command — double-clickable GUI launcher for macOS.
#
# Uses osascript folder pickers to gather inputs, then calls `dj-edit run`
# in a Terminal window. Opens the output folder in Finder when done.
set -uo pipefail

# Self-locate the dj-edit installation
_realpath() {
    python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$1"
}

HERE="$(dirname "$(_realpath "$0")")"
# If running from /Applications/, look for the brew-installed share dir
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
    osascript -e "display alert \"dj-edit not found\" message \"Could not locate the dj-edit installation. Tried: $HERE/bin/dj-edit\nReinstall with: brew install yolan/dj-edit/dj-edit\""
    exit 1
fi

# Helper: osascript folder picker. Returns POSIX path or empty if user cancelled.
pick_folder() {
    local prompt="$1"
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to POSIX path of (choose folder with prompt "$prompt")
    return chosen
on error
    return ""
end try
APPLESCRIPT
}

pick_file() {
    local prompt="$1"
    local types="$2"  # e.g. {"wav", "flac", "mp3", "m4a"}
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to POSIX path of (choose file with prompt "$prompt" of type $types)
    return chosen
on error
    return ""
end try
APPLESCRIPT
}

pick_text() {
    local prompt="$1"
    local default="$2"
    osascript <<APPLESCRIPT 2>/dev/null
try
    set chosen to text returned of (display dialog "$prompt" default answer "$default")
    return chosen
on error
    return ""
end try
APPLESCRIPT
}

# Welcome
osascript -e 'display dialog "dj-edit\n\nAuto-edit your DJ set into vertical TikTok-ready cuts.\n\nWe will ask for:\n  1. A project folder (where outputs go)\n  2. Your master audio file (WAV/FLAC)\n  3. Your Sony A7-III footage (a file or folder)\n  4. Optional: Insta360 + DJI footage\n\nClick OK to begin." buttons {"Cancel", "OK"} default button "OK"' >/dev/null 2>&1
[ $? -ne 0 ] && exit 0

# 1. Project name
PROJ_PARENT=$(pick_folder "Choose a folder where your project will be created:")
if [ -z "$PROJ_PARENT" ]; then exit 0; fi

PROJ_NAME=$(pick_text "Project name (no spaces):" "my-dj-set-$(date +%Y%m%d)")
if [ -z "$PROJ_NAME" ]; then exit 0; fi

PROJECT="$PROJ_PARENT$PROJ_NAME"

# 2. Master audio
AUDIO=$(pick_file "Choose your master audio file (the clean mixer recording):" '{"wav","flac","mp3","m4a","aiff"}')
if [ -z "$AUDIO" ]; then exit 0; fi

# 3. Sony A7-III
A7III=$(pick_file "Choose your Sony A7-III footage file (or Cancel to skip):" '{"mp4","mov","mts"}')

# 4. Optional Insta360
INSTA360=""
osascript -e 'try
display dialog "Do you have Insta360 footage to include?\n\n(Must be exported as equirectangular MP4 from Insta360 Studio first.)" buttons {"No", "Yes"} default button "No"
return button returned of result
on error
return "No"
end try' | grep -q "Yes" && INSTA360=$(pick_file "Choose your Insta360 equirectangular MP4:" '{"mp4","mov"}')

# 5. Optional DJI
DJI=""
osascript -e 'try
display dialog "Do you have DJI Action footage to include?" buttons {"No", "Yes"} default button "No"
return button returned of result
on error
return "No"
end try' | grep -q "Yes" && DJI=$(pick_file "Choose your DJI Action MP4:" '{"mp4","mov"}')

# Confirm
SUMMARY="Project: $PROJECT\nAudio:   $AUDIO"
[ -n "$A7III" ]   && SUMMARY="$SUMMARY\nA7-III:  $A7III"
[ -n "$INSTA360" ] && SUMMARY="$SUMMARY\nInsta360: $INSTA360"
[ -n "$DJI" ]     && SUMMARY="$SUMMARY\nDJI:      $DJI"
SUMMARY="$SUMMARY\n\nRender will take 20-40 min depending on set length. OK to proceed?"

osascript -e "display dialog \"$SUMMARY\" buttons {\"Cancel\", \"Render\"} default button \"Render\"" >/dev/null 2>&1
[ $? -ne 0 ] && exit 0

# Build the dj-edit invocation
INGEST_ARGS=("--audio=$AUDIO")
[ -n "$A7III" ]    && INGEST_ARGS+=("--a7iii=$A7III")
[ -n "$INSTA360" ] && INGEST_ARGS+=("--insta360=$INSTA360")
[ -n "$DJI" ]      && INGEST_ARGS+=("--dji=$DJI")

# Run in a visible Terminal so user sees progress
TERM_SCRIPT=$(mktemp /tmp/dj-edit-run-XXXXXX.sh)
cat > "$TERM_SCRIPT" <<TERMSCRIPT
#!/usr/bin/env bash
set -e
echo "=== dj-edit running ==="
echo "Project: $PROJECT"
echo ""
"$DJ" init "$PROJECT"
"$DJ" ingest "$PROJECT" ${INGEST_ARGS[@]}
"$DJ" run "$PROJECT"
echo ""
echo "=== DONE ==="
ls -lh "$PROJECT/out/"
osascript -e 'display notification "dj-edit finished!" with title "dj-edit"'
open "$PROJECT/out/"
echo ""
echo "Press any key to close this window..."
read -n 1
TERMSCRIPT
chmod +x "$TERM_SCRIPT"

# Open Terminal with the script
open -a Terminal "$TERM_SCRIPT"

exit 0
