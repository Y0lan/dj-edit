# Troubleshooting

The 10 most likely failures and how to fix them.

## 1. "VAAPI device /dev/dri/renderD128 not found"

You're on Linux without an Intel/AMD GPU exposed at the standard render node. Either pass `--encoder=x264` (slower, works everywhere) or, if you ARE on Mac, this error shouldn't appear — `--encoder=auto` should pick `videotoolbox`.

```bash
dj-edit render my-set --aspect=9x16 --encoder=x264
```

## 2. ".command file is from an unidentified developer" (macOS)

Right-click the `dj-edit-mac.command` file in Finder → **Open** → confirm in the dialog. One-time prompt; subsequent launches work normally.

## 3. "audio missing: <path>"

`manifest.json` references an audio file that no longer exists. Re-run `dj-edit ingest` with the correct `--audio=PATH`.

## 4. "no candidate hook events (no drop/peak with coverage)"

`pick-hook.py` couldn't find any detected drop/peak with full camera coverage. Likely your camera covers only a partial master timeline. Skip this step — the main pipeline doesn't require a hook:

```bash
dj-edit edl my-set    # skip 'hook' subcommand
dj-edit render my-set --aspect=9x16
```

## 5. "ERROR: zero clips in EDL"

`build-edl.py` walked the timeline but couldn't emit any clips. Causes:
- Camera coverage doesn't overlap with detected events (check `offsets.json` and `events.json`).
- Sync produced `a=1, b=0` silently (zero correlation points). Check `sync` stderr.
- Master audio is too short or too quiet.

Diagnosis:
```bash
jq '.events | group_by(.type) | map({type: .[0].type, count: length})' my-set/events.json
jq '.a7iii[0].coverage, .a7iii[0].inliers' my-set/offsets.json
```

## 6. "aspect 1.778 suggests this is NOT a 360 equirectangular export"

Your `--insta360=PATH` is pointing at a flat reframed export (aspect ≈ 16:9), not a 360 equirectangular (aspect = 2:1). Either:
- Re-export from Insta360 Studio with "Export 360° equirectangular"
- OR if it IS a flat export from the camera, ingest as `--a7iii=PATH` instead (treats it as a regular camera).

## 7. "librosa.beat.beat_track returned tempo of 64 BPM"

The beat tracker locked on the half-time grid. Pass a tempo hint:

```bash
dj-edit analyze my-set --start-bpm=140
```

Common values: techno 128-140, drum & bass 170-180, dubstep 140, progressive house 124-128.

## 8. Render finishes but output has a/v desync

Likely cause: events outside camera coverage caused silent skip. The fixed `audio_master_start/end` in EDL meta should prevent this. Check:

```bash
jq '.audio_master_start, .audio_master_end' my-set/edl_9x16.json
```

These define the master audio window. If your render's audio runs longer than the video, the `--/filter_complex` graph isn't honoring these — file a bug.

## 9. "ModuleNotFoundError: No module named 'lib'"

`render.sh`'s Python heredoc needs to find `lib/selector.py` etc. It uses `$DJ_EDIT_ROOT` environment variable. If you're running from a non-standard install location, ensure `DJ_EDIT_ROOT` points at the directory containing `bin/`, `lib/`, `presets/`.

## 10. "Conversion failed!" from ffmpeg with `[Parsed_concat_X] Input link ... SAR ... do not match"

SAR mismatch between concat inputs. Fix is already in render.sh (`setsar=1` on each clip). If you see this error, your render.sh may be from an old version — `git pull` and re-run.

## Where to look when things break

- `events.json` — was the audio analyzed correctly? Reasonable drop count (10-30 for a 90-min set)?
- `offsets.json` — did sync find your camera? `inliers / points` should be > 50%.
- `edl_9x16.json` — is `clips` non-empty? Does `audio_master_end - audio_master_start` match the camera's coverage interval?
- `cache/loudnorm_*.json` — did the two-pass loudnorm measurement succeed?

If still stuck, open a GitHub issue with these four files attached.
