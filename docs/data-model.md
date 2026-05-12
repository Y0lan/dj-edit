# Data Model

Every pipeline stage emits JSON. This is the schema.

## `manifest.json`

Written by `dj-edit ingest`. Records the inputs.

```json
{
  "insta360_projection": "e",
  "target_fps": 25,
  "cameras": {
    "insta360": [
      {
        "path": "footage/insta360/clip1.mp4",
        "width": 5760, "height": 2880, "fps": 30.0,
        "duration": 1800.5, "aspect": 2.0, "codec": "h264"
      }
    ],
    "a7iii": [
      {"path": "footage/a7iii/C0004.MP4", "width": 3840, "height": 2160,
       "fps": 25.0, "duration": 5607.4, "aspect": 1.778, "codec": "h264"}
    ],
    "dji": []
  },
  "audio": {
    "path": "footage/audio/master.wav",
    "duration": 5745.0, "sample_rate": 48000, "channels": 2, "codec": "pcm_f32le"
  }
}
```

## `events.json`

Written by `dj-edit analyze`. The audio-analysis output.

```json
{
  "meta": {
    "duration": 5745.0,
    "tempo": 128.0,
    "n_beats": 12252,
    "sub_p30_db": -45.0,
    "broad_p80_db": -8.5
  },
  "events": [
    {"t": 23.3, "type": "drop", "duration": 0.5, "intensity": 1.0, "confidence": 0.99},
    {"t": 294.2, "type": "drop", "duration": 0.5, "intensity": 1.0, "confidence": 0.85},
    {"t": 100.0, "type": "build", "duration": 8.0, "intensity": 0.7, "confidence": 0.6},
    {"t": 200.0, "type": "breakdown", "duration": 16.0, "intensity": 0.6, "confidence": 0.7},
    {"t": 1000.0, "type": "peak", "duration": 30.0, "intensity": 0.8, "confidence": 0.6}
  ]
}
```

Event types: `drop`, `build`, `breakdown`, `peak`. Intensity in [0, 1]; confidence in [0, 1].

## `beats.json`

Written by `dj-edit analyze`.

```json
{
  "tempo": 128.0,
  "beats": [
    {"t": 0.469, "confidence": 0.7},
    {"t": 0.938, "confidence": 0.7}
  ]
}
```

## `offsets.json`

Written by `dj-edit sync`. Per-camera affine sync map.

```json
{
  "a7iii": [
    {
      "path": "C0006.MP4",
      "duration": 5607.4,
      "a": 1.0,
      "b": 530.8,
      "points": 19,
      "inliers": 8,
      "coverage": [530.8, 5745.0]
    }
  ],
  "insta360": [],
  "dji": []
}
```

`master_t = a*camera_t + b`. Coverage is the master_t range where this camera has valid audio overlap. `inliers / points` is sync quality (>50% = solid).

## `hook.json`

Written by `dj-edit hook` (optional, for v1.1 highlight reels). Not consumed by main EDL builder.

```json
{
  "src": "C0006.MP4", "camera": "a7iii",
  "master_t": 636.6, "src_in": 105.8,
  "duration": 2.0, "treatment": "flash_freeze",
  "title": null, "event_type": "drop", "event_intensity": 1.0, "score": 1.2
}
```

## `shot_scores.json`

Written by `dj-edit score`. Per-camera-per-second motion energy.

```json
{
  "a7iii": [
    {"path": "C0006.MP4",
     "scores": [{"t": 0.0, "motion": 12.3}, {"t": 1.0, "motion": 14.5}]}
  ]
}
```

`motion` is mean absolute frame difference (`ffmpeg tblend=difference + signalstats YAVG`), 0-32 range.

## `edl_<aspect>.json`

Written by `dj-edit edl`. The edit decision list per output aspect.

```json
{
  "aspect": "9x16",
  "target_fps": 25,
  "src_w": 3840, "src_h": 2160,
  "style": "aggressive",
  "audio_master_start": 531.3,
  "audio_master_end": 5533.0,
  "clips": [
    {
      "in": 0.0, "out": 3.6,
      "master_in": 531.3, "master_out": 534.9,
      "src": "C0006.MP4", "camera": "a7iii",
      "src_in": 0.5,
      "framing": "medium", "treatment": null,
      "transition_out": "hard"
    }
  ]
}
```

- `in`, `out` are output timeline coordinates (start at 0).
- `master_in`, `master_out` are master audio coordinates.
- `audio_master_start/end` define the master audio window that maps onto the output timeline. `render.sh` uses these to trim master audio so video and audio align.
- `framing` ∈ {wide, medium, tight, left, right, up, punch}; see `lib/selector.py`.
- `treatment` ∈ {flash_freeze, triple_punch, speed_ramp_into_drop, color_pop, whip_blur, null}; see `lib/treatments.py`.
- `move` (only on `insta360` camera clips) ∈ {orbit, tilt-reveal, dolly-zoom, whip-pan, crowd-reveal}; see `presets/moves/`.

## `.cmd` files (sendcmd presets for v360@mv)

Written by `lib/moves.py` or hand-rolled in `presets/moves/`. ffmpeg `sendcmd` format with `[expr]` flag.

```
0.000-8.000 [expr] v360@mv yaw 45.0*TI;
0.000-8.000 [expr] v360@mv pitch -5.0;
0.000-8.000 [expr] v360@mv h_fov 90.0;
```

- `T` = timeline time in seconds.
- `TI` = interval time (resets per command range).
- `v360@mv` is the named v360 filter instance in render.sh's graph.
