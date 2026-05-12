# Changelog

All notable changes to dj-edit are documented here.

## [0.1.2] — 2026-05-12

First friend-feedback patch. Two real bugs hit on macOS install.

### Fixed
- **Brew install crash on `/Applications`**. The formula tried to write
  `dj-edit-mac.command` directly to `/Applications` via Ruby's `File.write`,
  which fails inside brew's install sandbox. Friend had to manually patch
  the formula to skip that step. Fix: install the .command into `share/`
  only; `caveats` instruct the user to copy it to `/Applications` themselves
  (one extra `cp` line, but no install crash).
- **Doctor false-positive on filter detection**. Friend's `dj-edit doctor`
  reported `loudnorm` (and earlier `drawtext`) as missing even though
  `ffmpeg -filters | grep loudnorm` showed them present. Root cause: the
  regex `^ ?[.TS]+ +<name> +` was sensitive to ffmpeg's leading-whitespace
  pattern, which varies across versions/builds. Fix: parse column 2
  directly with awk (`$2 == filter_name`). Same fix on the encoder check.

### Upgrade for existing users

```
brew upgrade dj-edit
```

## [0.1.1] — 2026-05-12

Quality + resilience pass. No new user-facing features; faster re-runs and confidence in correctness.

### Added
- **Output-level render cache** in `bin/render.sh`. Keyed on `sha256(canonical_edl_json)`.
  Re-runs with the same EDL skip the encode entirely and copy from cache (<1s vs 25-40 min).
- **41 pytest unit tests** across `lib/treatments`, `lib/selector`, `lib/moves`, and
  `bin/sync-cameras.robust_offset`. Catches regressions in pure logic without needing
  real video fixtures.
- **GitHub Actions CI**: matrix on `ubuntu-latest` + `macos-latest` × Python 3.11/3.12.
  Runs pytest, bash/python syntax checks, and `dj-edit quickstart --demo` smoke test.
- **`scripts/release.sh`** one-command release helper. Bumps version, tags, creates
  GitHub release, computes tarball SHA256, updates the brew formula in the tap.

### Fixed
- `robust_offset` in `bin/sync-cameras.py` now takes optional `cam_dur` to filter out
  clipping-at-master-end artifacts more aggressively. Caught by `test_sync_helpers.py`.
- Attempted a per-chunk render cache approach for mid-render resume, but reverted due to
  3-5x slowdown vs monolithic (per-chunk decode setup on a 64GB source doesn't share work).
  Output-level cache is the cleaner solution for the common iteration case.

### Notes
- v0.2 will add: per-camera LUTs (color match), Songrec track-ID burn-in, `--style`
  knob that actually changes pacing, watermark + endcard. See `lexical-percolating-bee.md`
  in the plan archive for full backlog.

## [0.1.0] — 2026-05-12

Initial public release.

### Added
- Single-camera Sony A7-III + Tascam recorder workflow
- Multi-feature audio analysis (librosa onset + band RMS + spectral centroid + beat_track)
- Affine camera sync via onset-envelope cross-correlation with median-offset estimator
- EDL builder with event-driven treatments and beat-snapped sub-beat cuts
- Virtual-camera reframing (wide/medium/tight/left/right/up/punch) from a single source
- Drop impact treatments: flash_freeze zoom-punch, triple_punch, speed_ramp_into_drop
- Two-pass loudnorm audio normalization with mtime-keyed cache
- Multi-aspect render (9:16 + 16:9) via one `filter_complex` graph (no concat demuxer)
- Platform auto-detect encoder: `videotoolbox` (macOS) / `vaapi` (Linux) / `libx264` (fallback)
- Per-drop clip extraction with timestamp + intensity in filename
- Top-6 drops highlight reel concatenated via concat demuxer
- ERP equirectangular validation on Insta360 inputs
- Multi-camera support (Insta360 + DJI + A7-III) in beta

### Fixed
- Hardcoded `/home/yolan/Projects/dj-edit/` absolute paths replaced with `$DJ_EDIT_ROOT`
- BSD `readlink -f` incompatibility (macOS) — portable `_realpath()` shim via Python
- bash 3.2 incompatibility (macOS default) — `mapfile` replaced with `while read`
- v360 animation: replaced invalid `eval=frame` expression with sendcmd-driven `v360@mv`
- `scipy.signal.fftconvolve` → `scipy.signal.correlate` (convolution vs correlation sign)
- Hook-prepend a/v desync (hook removed from main EDL, reserved for v1.1 highlight reel)
- Coverage skip-without-advance causing video to drift behind audio
- librosa `beat_track` half-time lock via `start_bpm=128` hint
- `librosa.beat.beat_track(units='time')` (was defaulting to `units='frames'`)
- `flash_freeze` and `triple_punch` zoompan: `t` → `ot` (output time)
- `flash_freeze` `tpad` removed (was extending clip duration past EDL)
- `speed_ramp_into_drop` division by zero at default slowmo_factor
- Selector crop dimensions/offsets now even-rounded for YUV420 encoder compatibility
- `.insv` files now properly rejected at ingest (was silently skipped)
- `ingest.sh` audio path tracked explicitly (was picking first file in dir)
- `sync-cameras.py` short-camera case + zero-points failure (was silently a=1, b=0)
- `pick-hook.py` coverage check now validates full hook duration
- `render.sh` empty EDL → `concat=n=0` crash, now clean error
- `render.sh` `-filter_complex_script` (deprecated) → `-/filter_complex <file>`
- `render.sh` `setsar=1` on each clip prevents SAR mismatch concat failure
- `render.sh` `while read || [ -n "$line" ]` reads last line without trailing newline
- `render.sh` atomic `<out>.partial.mp4` → `<out>.mp4` rename on success
- `render.sh` `jq` calls replaced with Python json (macOS doesn't ship jq)
- drawtext properly escapes `:`, `\`, `,`, `%`, `'` in title text

### Known limitations (planned for v0.2)
- No track ID via Songrec / Shazam
- No per-camera LUTs (identity placeholders shipped)
- No watermark / endcard burn-in
- No per-platform encoder presets (TikTok / Reels / YouTube)
- No `--style aggressive|clean|signature` presets (flag exists but is metadata-only)
- No HDR→SDR tonemap for A7-III S-Log/HLG inputs
- No `dj-edit quickstart` subcommand yet (planned)
- No `dj-edit doctor` subcommand yet (planned)
- No `dj-edit-mac.command` osascript wrapper yet (planned)
- No per-clip render cache (full re-render on Ctrl+C)
- No automated tests / CI
