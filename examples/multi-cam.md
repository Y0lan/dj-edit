# Example: Multi-camera (Insta360 ERP + Sony A7-III + DJI Action) — BETA

The full multi-camera path. **Status: alpha**, less tested than single-camera. Expect rough edges.

## Inputs

- 1× Insta360 X3/X4 footage **already exported as equirectangular MP4** (2:1 aspect). Raw `.insv` is rejected — open Insta360 Studio and "Export 360°" first.
- 1× Sony A7-III, 4K @ 25/30fps
- 1× DJI Action 4K @ 60fps
- 1× master audio WAV

## Walkthrough

```bash
dj-edit init my-multi-set

dj-edit ingest my-multi-set \
    --insta360=/path/to/insta360-exported.mp4 \
    --a7iii=/path/to/Sony/C0004.MP4 \
    --dji=/path/to/DJI/DJI_0001.MP4 \
    --audio=/path/to/master.wav

dj-edit run my-multi-set
```

## What changes vs single-camera

- **Camera selection**: at each cut, the EDL builder picks the camera with the highest motion-energy score that has valid coverage. Round-robin avoidance prevents picking the same camera twice in a 4s window.
- **Insta360 reveals**: during breakdowns, the EDL emits an Insta360 clip with an animated `v360` reframe — `tilt-reveal`, `orbit`, `dolly-zoom`, `whip-pan`, or `crowd-reveal`. Driven by `sendcmd` against the `v360@mv` named filter instance.
- **Sync per camera**: each camera is independently synced against the master. Coverage intervals may not fully overlap — the EDL clamps to the master_t range that has at least one camera covering.

## Known issues in v0.1.0

- Color matching across cameras is identity LUTs (no real per-camera grade). A7-III S-Log will look flat next to DJI D-Cinelike contrasty. Apply your own LUTs in v0.2 by replacing `presets/luts/*.cube`.
- Per-clip width/height isn't yet stored per-source. If cameras have different resolutions, the crop calculation uses the first camera's dimensions for all — works fine for same-resolution but breaks mixed.
- Insta360 frame-rate mismatch: X3 caps at 5.7K30. If `target_fps=30` is forced when other sources are 25, you'll get duplicated frames. Override with `--target-fps=25` if all sources are PAL.

## Tips

- Pre-export Insta360 to ERP MP4 BEFORE running `dj-edit ingest`. The pipeline does not stitch raw `.insv` (no Linux/Mac CLI exists for that).
- If sync produces low inlier counts (< 5/N) for a camera, that camera's scratch audio is too quiet or out of phase. Try recording a clap at the start of the set as a sync reference.
