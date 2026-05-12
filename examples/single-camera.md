# Example: Single Sony A7-III + Tascam master recording

The default-tested workflow. One camera, one master audio file.

## Inputs

- 1× Sony A7-III file (`C0004.MP4` etc.), 4K @ 25fps or 30fps, on-camera audio
- 1× master audio WAV from a Tascam-class recorder (mixer line-out feed, 48 kHz)

## Walkthrough

```bash
# 1. Create project
dj-edit init my-set-2026-02-28

# 2. Ingest (symlinks the camera + audio into the project)
dj-edit ingest my-set-2026-02-28 \
    --a7iii=/Volumes/SDCARD/PRIVATE/M4ROOT/CLIP/C0004.MP4 \
    --audio=/Volumes/Tascam/master_recording.wav

# 3. Run the pipeline (analyze + sync + edl + render + deliver)
dj-edit run my-set-2026-02-28
```

## What gets analyzed

`dj-edit analyze` reads the master WAV and emits:

- `events.json` — detected drops, builds, breakdowns, peaks
- `beats.json` — beat-grid timestamps + tempo

`dj-edit sync` cross-correlates the camera's on-camera audio against the master and emits:

- `offsets.json` — per-camera affine sync map `master_t = a*camera_t + b` plus coverage interval

## What gets rendered

`my-set-2026-02-28/out/` contains:

- `set_9x16_25fps.mp4` — full set, vertical (1080×1920), all drops/builds/breakdowns cut with virtual-camera framings
- `set_16x9_25fps.mp4` — full set, landscape
- `drops/drop_03_t30m47s_int091.mp4` — each detected drop as a standalone clip with timestamp + intensity in the name
- `drops/INDEX.txt` — human-readable tracklist
- `highlight_9x16.mp4` — top 6 drops concatenated

## Iteration

Tweak the EDL before re-rendering:

```bash
# Re-build EDL only (keeps analyze/sync results)
dj-edit edl my-set-2026-02-28

# Re-render only one aspect
dj-edit render my-set-2026-02-28 --aspect=9x16
```

## Tips

- If `events.json` has too many drops, edit `bin/analyze-audio.py` defaults: `--max-drops=15` instead of 25.
- If sync inliers < 50%, your camera audio is too noisy. Try a manual offset via `offsets.json` directly.
- Render takes ~5x real-time on M-series Mac (videotoolbox) or Intel Arc (vaapi). For a 90-min set, expect ~18 min per aspect.
