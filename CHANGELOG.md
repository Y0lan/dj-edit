# Changelog

All notable changes to dj-edit are documented here.

## [0.2.1] — 2026-05-13

Third friend-feedback patch. He ran `dj-edit run` on his real footage and
texted "can't find where the final video is."

### Fixed
- **`dj-edit run` and `dj-edit deliver` now auto-open the output folder
  when done.** Previously only `dj-edit quickstart` did this. Both
  commands now print the location + `open out/` on macOS (and `xdg-open`
  on Linux only when a display is attached, to avoid hanging headless).
- **`dj-edit score-360` subcommand placeholder** — v0.3+ will ship a real
  implementation (content-aware Insta360 yaw scoring). v0.2.1 dispatcher
  handles "script not yet installed" gracefully so the `run` chain can
  evolve in v0.3 without breaking v0.2.x users.

### Upgrade

```
brew upgrade dj-edit
```

## [0.2.0] — 2026-05-12

The Insta360 movement upgrade. v0.1.x reframed via static stock moves (orbit at
constant speed, dolly-zoom linear); v0.2 makes camera motion cinematic and
music-aware.

### Added — Tier A: eased motion primitives

Every move (`orbit`, `tilt_reveal`, `dolly_zoom`, `crowd_reveal`) now uses
cosine ease-in-out instead of linear ramps. Two new combined-motion primitives:
- **`counter_motion`**: yaw turns one direction while pitch oscillates and h_fov
  breathes. Three orthogonal motions from one cheap source.
- **`dolly_with_drift`**: push-in plus slow yaw drift. Subject-lock effect.

### Added — Tier B: music-driven moves

- **`bpm_sync_orbit`**: yaw rotates exactly N bars per full turn, BPM-locked.
- **`kick_pulse_fov`**: h_fov pumps at BPM frequency for subliminal beat sync.
- **`drop_impact`**: 4-phase preset (freeze → whip → dolly-zoom → hold) for
  the moment of impact.
- **`build_tension`**: quadratic-ease h_fov tightening into the drop.
- **`breakdown_drift`**: ultra-slow yaw + slow pitch tilt for breakdowns.

### Added — Tier D: stochastic move pools

- **`lib/selector.pick_move_for_event`**: weighted RNG pick, intensity-biased,
  with 3-pick avoid-repeat window. Same set rendered twice → different cuts.
- **`presets/styles/{aggressive,clean,signature}.json`**: weight overrides.
  `aggressive` ships drop_impact every drop + whip-pans dominating; `clean` is
  cinematic and slow; `signature` is the balanced default.
- **`presets/references/{anyma,fisher,rinse}.json`**: artist-style packs.
- `--style`, `--reference`, `--seed` CLI flags on `dj-edit edl`.

### Fixed — codex round 1 + 2 (CRITICAL)

- **`TI` is normalized [0,1] in sendcmd, NOT seconds.** Rewrote every move's
  math to use `T` (absolute seconds) instead of bare `TI`. The v0.1 expressions
  like `45*TI` meant "45 degrees TOTAL" — not "45 deg/sec" as designed.
- **Commas inside sendcmd expressions break parsing** (verified in ffmpeg
  n8.1.1). Replaced piecewise `if(lt(x,a),b,c)` with multi-line time-windowed
  commands (each command on its own line, no nested if). Replaced `pow(T,2)`
  with `T*T`.
- **Render cache only hashed EDL JSON, not cmd file contents.** Tweaking a
  move generator while EDL JSON stayed byte-identical → cache served stale
  video. Now hashes EDL plus every referenced `.cmd` file's content.
- **`_generate_move_cmd` returned a relative cmd path.** Broke when `render.sh`
  was invoked from a different cwd. Now returns `.resolve()` absolute path.
- **`target_yaw or 60.0`** made explicit `target_yaw=0.0` impossible. Now uses
  `target_yaw is None` check.
- **`load_pool_overrides` silently fell back to defaults on `--style typo`.**
  Now warns to stderr (with the resolved file path) for missing pack files,
  malformed JSON, or pack entries referencing unknown move names.
- **Intensity not clamped.** Negative or >1 event intensities could produce
  zero or negative weights in pool selection. Clamped to [0, 1].

### Added — Tests + CI hardening

- **131 pytest tests** (up from 41 in v0.1.x). Includes:
  - 21 lib/moves invariant tests (`no-pow`, `no-if`, `no-commas`, `uses-T`)
  - 14 selector pool-selection tests (weight bias, avoid-repeat, packs load)
  - **60 shipped-preset hygiene tests** (12 presets × 5 invariants) — this
    test set automatically catches the codex round 1 regression class (where
    presets weren't regenerated after a lib rewrite).
- Full quickstart `--demo` end-to-end smoke verified: drop detected, set_9x16
  + set_16x9 + highlight + per-drop clip all produced in ~8 seconds.

### Verified end-to-end

All 12 generated `.cmd` files render through real ffmpeg n8.1.1 sendcmd (orbit,
drop-impact, build-tension, kick-pulse-fov tested with rgbtestsrc input;
frames at t=0 and t=1s have different md5sums → motion confirmed).

### Upgrade

```
brew upgrade dj-edit
```

## [0.1.3] — 2026-05-12

Second friend-feedback patch. `dj-edit quickstart --demo` now actually completes
end-to-end with a detected drop and a rendered per-drop clip.

### Fixed
- **`demo_video.mp4` had no audio track**. `dj-edit sync` requires camera scratch
  audio to cross-correlate against the master — without it, no offsets, empty
  EDL, zero clips. Fix: bake the demo audio INTO the demo video so sync resolves
  trivially.
- **Synthesized drop didn't trigger detection**. The previous demo "drop" at
  t=15s was a slow volume ramp, which librosa's `onset_strength` saw as nothing
  remarkable. Fix: regenerate `demo_audio.wav` with a sharp sub-bass impact at
  t=15s plus a noise impulse for the onset trigger. Analyze now finds:
  `build @ 7s + drop @ 15s (intensity 1.0)`.

### Result
`dj-edit quickstart --demo` now produces in 8.6s:
- `set_9x16.mp4` + `set_16x9.mp4` (full 30s timeline)
- `highlight_9x16.mp4` (drop reel)
- `drops/drop_01_t00m15s_int100.mp4` (timestamp + intensity in filename)
- `drops/INDEX.txt` (tracklist)

A proper smoke test of the install.

### Upgrade

```
brew upgrade dj-edit
```

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
