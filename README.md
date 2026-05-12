# dj-edit

**Auto-edit your DJ-set videos into TikTok-ready cuts.** Drops a folder of footage and a master audio recording in, ships a 9:16 vertical reel of every drop in the set with beat-snapped cuts, zoom punches, and color-matched framings. Designed for solo Sony A7-III + Tascam recordings; multi-camera (Insta360 ERP + DJI Action) works as beta.

> Status: v0.1.0, alpha. Single-camera path tested. Multi-camera path exists in code but not yet polished.

## What it does

1. **Analyzes** the master audio with librosa: detects drops, builds, breakdowns, peaks, beats.
2. **Syncs** each camera to the master via onset-envelope cross-correlation (handles offset + clock drift).
3. **Builds an EDL** (edit decision list) per aspect ratio (9:16 + 16:9), with:
   - Beat-snapped cuts during peaks
   - Sub-beat machine-gun cuts at builds
   - Flash-freeze zoom punches on drop impact frames
   - Virtual-camera reframing (wide / medium / tight / left / right / punch) from one 4K source
4. **Renders** via ffmpeg `filter_complex`, two-pass loudnorm, hardware-accelerated encode:
   - Apple Silicon / Intel Mac → `h264_videotoolbox`
   - Linux with Intel/AMD GPU → `h264_vaapi`
   - Fallback → `libx264`
5. **Delivers** the full set (9:16 + 16:9) plus extracted per-drop clips with timestamp-encoded names and a highlight reel of the top 6 drops.

## Install (macOS, recommended)

```bash
brew tap Y0lan/dj-edit
brew install dj-edit
```

Brew handles ffmpeg, jq, python3.11, and the venv. Then double-click `/Applications/dj-edit-mac.command` to launch the folder picker.

> First launch: macOS Gatekeeper will warn about "unidentified developer". Right-click the file → Open → confirm once. After that, double-click works normally.

## Install (Linux or from source)

```bash
git clone https://github.com/Y0lan/dj-edit.git
cd dj-edit
./setup.sh
```

`setup.sh` detects your platform and installs ffmpeg + python3 + venv deps via the right package manager (pacman, apt, dnf, or brew).

## Quickstart — see a video in 5 minutes

Once installed, with no own footage yet, you can try the bundled demo:

```bash
dj-edit quickstart --demo
```

That synthesizes a 30s test mix + matching video, runs the full pipeline, and opens the resulting drop clip. If you see a video play, the install works.

## Real workflow

```bash
# 1. Create a project
dj-edit init my-set

# 2. Ingest your footage + audio
dj-edit ingest my-set \
    --a7iii=/path/to/Sony/C0004.MP4 \
    --audio=/path/to/master_mix.wav

# 3. Run the full pipeline
dj-edit run my-set
```

When done, `my-set/out/` contains:

- `set_9x16.mp4` — full set, vertical, ready for TikTok/Reels/Shorts
- `set_16x9.mp4` — full set, landscape, for YouTube
- `drops/drop_03_t30m47s_int091.mp4` — every detected drop as a standalone clip, named with its timestamp and intensity score
- `drops/INDEX.txt` — human-readable tracklist of drops
- `highlight_9x16.mp4` — the top 6 drops concatenated into one reel

## Multi-camera (beta)

Add Insta360 (equirectangular MP4 — export from Insta360 Studio first) and DJI Action sources alongside your A7-III:

```bash
dj-edit ingest my-set \
    --insta360=/path/to/Insta360/360-exported.mp4 \
    --a7iii=/path/to/Sony/C0004.MP4 \
    --dji=/path/to/DJI/DJI_0001.mp4 \
    --audio=/path/to/master_mix.wav
```

The pipeline auto-picks the best camera per cut, with motion-energy scoring and round-robin avoidance. Insta360 footage gets animated yaw/pitch reveals during breakdowns and whip-pan transitions into drops via the ffmpeg `v360` filter driven by `sendcmd`. **Note**: multi-camera path is alpha — sync and color matching may need manual tweaks.

## Architecture

```
dj-edit/
├── bin/
│   ├── dj-edit              # main dispatcher
│   ├── ingest.sh            # symlinks footage, validates ERP, builds manifest
│   ├── analyze-audio.py     # librosa multi-feature event detection
│   ├── sync-cameras.py      # onset-envelope cross-correlation per camera
│   ├── pick-hook.py         # cold-open candidate selector (v1.1)
│   ├── score-shots.py       # motion-energy per camera per second
│   ├── build-edl.py         # walks timeline, emits aspect-specific EDLs
│   └── render.sh            # filter_complex pipeline with platform-aware encode
├── lib/
│   ├── moves.py             # sendcmd .cmd file generators for v360@mv
│   ├── selector.py          # camera/framing selection + crop math
│   └── treatments.py        # flash_freeze, triple_punch, speed_ramp, color_pop
├── presets/
│   └── moves/               # *.cmd files for orbit, tilt-reveal, dolly-zoom, whip-pan
├── examples/
│   └── demo/                # bundled synthesized test fixture (30s WAV + MP4)
├── tests/                   # pytest unit tests
└── docs/                    # data model, troubleshooting, macOS install guide
```

See `docs/data-model.md` for JSON schemas (`events.json`, `edl_*.json`, etc.).

## Troubleshooting

- **"VAAPI device not found"**: you're on Linux without an Intel/AMD GPU at `/dev/dri/renderD128`. Re-run with `--encoder=x264` (slower but works) or `--encoder=videotoolbox` if you happen to be on macOS.
- **"audio missing"**: re-run `dj-edit ingest` with the correct `--audio=PATH`.
- **"no clips" / "ERROR: zero clips in EDL"**: no camera coverage overlaps with detected audio events. Check `offsets.json` for sync issues — likely the camera audio is too noisy or out of master's time range.
- **"aspect 1.778 suggests this is NOT a 360 equirectangular export"**: your Insta360 export is reframed flat, not raw 360°. Either re-export from Insta360 Studio with "Export 360° equirectangular", OR ingest as `--a7iii` instead of `--insta360`.
- **Macos: ".command file is from an unidentified developer"**: right-click → Open → Open. One-time prompt.

See `docs/troubleshooting.md` for the full list.

## Status

- ✅ Single-camera Sony A7-III + Tascam workflow (tested on real 90+ min sets)
- ✅ Mac install via brew tap with auto-detect encoder
- ✅ 9:16 + 16:9 output, per-drop clip extraction, top-6 highlight reel
- 🟡 Multi-camera path (Insta360 + DJI + A7-III): code exists, not extensively tested
- 🟡 Color matching via LUTs: identity LUTs only, real per-camera LUTs in v0.2
- 🔴 No track ID / Shazam integration yet (planned v0.2)
- 🔴 No watermark / endcard yet (planned v0.2)

## License

MIT. See `LICENSE`.

## Acknowledgments

Built with [ffmpeg](https://ffmpeg.org/), [librosa](https://librosa.org/), [scipy](https://scipy.org/). Inspired by the gap between "shot a set" and "posted a clip" — usually a Premiere project that takes 4 hours. This makes it 4 minutes.
