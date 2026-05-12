#!/usr/bin/env python3
"""analyze-audio.py — multi-feature DJ-set event detection.

Reads master audio, emits events.json (drops/peaks/builds/breakdowns) and beats.json.
Uses onset strength + band-RMS + spectral features. RMS alone is unreliable on
mastered/compressed DJ audio.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import butter, sosfilt, medfilt

SR = 22050
HOP = 2205  # 100 ms hops at 22050 Hz


def band_rms(y: np.ndarray, sr: int, low: float, high: float) -> np.ndarray:
    """Bandpass RMS envelope in dB."""
    nyq = sr / 2
    sos = butter(4, [low / nyq, min(high / nyq, 0.999)], btype="band", output="sos")
    y_b = sosfilt(sos, y)
    rms = librosa.feature.rms(y=y_b, frame_length=HOP * 2, hop_length=HOP)[0]
    return 20.0 * np.log10(np.maximum(rms, 1e-8))


def detect_events(y: np.ndarray, sr: int,
                  max_drops: int = 25,
                  max_breakdowns: int = 15,
                  start_bpm: float = 128.0) -> tuple[list, list, dict]:
    # Beat tracking — units='time' is critical (not the default 'frames')
    # start_bpm hint prevents half-time/double-time locking on DJ music
    tempo, beats = librosa.beat.beat_track(
        y=y, sr=sr, hop_length=HOP, units="time", start_bpm=start_bpm,
    )
    tempo = float(np.atleast_1d(tempo)[0])

    # Onset strength — impact-frame signal, no RMS dependency
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onset_t = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr, hop_length=HOP)
    onset_smooth = medfilt(onset_env, kernel_size=11)

    # Band RMS (sub / mid / air) — sub-band drop is more reliable than broadband
    sub_db = band_rms(y, sr, 20, 120)
    mid_db = band_rms(y, sr, 120, 2000)
    air_db = band_rms(y, sr, 2000, 10000)
    broad_db = 20.0 * np.log10(np.maximum(
        librosa.feature.rms(y=y, frame_length=HOP * 2, hop_length=HOP)[0], 1e-8))
    times = librosa.frames_to_time(np.arange(len(broad_db)), sr=sr, hop_length=HOP)

    # Spectral brightness (useful for build detection)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]

    # Percentiles for thresholding
    sub_p30 = float(np.percentile(sub_db, 30))
    broad_p80 = float(np.percentile(broad_db, 80))
    onset_p70 = float(np.percentile(onset_env, 70))

    events: list[dict] = []
    n = len(broad_db)

    # ---- Breakdown: sustained sub-band quiet (>= 8s) ----
    quiet = sub_db < sub_p30
    raw_breaks = []
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            dur = times[j - 1] - times[i] if j > i else 0
            if dur >= 8.0:
                intensity = float((sub_p30 - sub_db[i:j].mean()) / 20)
                raw_breaks.append({
                    "t": float(times[i]), "type": "breakdown",
                    "duration": float(dur),
                    "intensity": max(0.1, min(1.0, intensity)),
                    "confidence": 0.7,
                })
            i = j
        else:
            i += 1
    # Keep only the most prominent breakdowns
    raw_breaks.sort(key=lambda b: b["duration"] * b["intensity"], reverse=True)
    events.extend(raw_breaks[:max_breakdowns])

    # ---- Drop detection: sub-band RMS rise > 6 dB AND onset spike top-decile ----
    onset_p90 = float(np.percentile(onset_env, 90))
    sub_smooth = medfilt(sub_db, kernel_size=5)
    raw_drops = []
    for k in range(10, n - 2):  # 10 hops = 1s lookback window
        rise = sub_smooth[k] - sub_smooth[k - 10]
        onset_here = onset_env[k] if k < len(onset_env) else 0
        if rise >= 6.0 and onset_here >= onset_p90:
            # Confirm sustained mid-band activity 3s after impact
            future_mid = mid_db[k:min(k + 30, n)]
            if future_mid.mean() > np.percentile(mid_db, 65):
                raw_drops.append({
                    "t": float(times[k]),
                    "type": "drop",
                    "duration": 0.5,
                    "intensity": float(min(1.0, rise / 14.0)),
                    "confidence": float(min(1.0, onset_here / (onset_p90 * 1.2))),
                    "_score": float(rise * onset_here),
                })
    # Coalesce raw_drops: greedy non-max suppression with 25s window
    raw_drops.sort(key=lambda d: d["_score"], reverse=True)
    kept_drops: list[dict] = []
    for d in raw_drops:
        if any(abs(d["t"] - k["t"]) < 25.0 for k in kept_drops):
            continue
        kept_drops.append(d)
        if len(kept_drops) >= max_drops:
            break
    for d in kept_drops:
        d.pop("_score", None)
    events.extend(kept_drops)

    # ---- Build: for each kept drop, find the build leading into it ----
    win = 60  # 60 hops = 6s
    drop_times = sorted([d["t"] for d in kept_drops])
    raw_builds = []
    for drop_t in drop_times:
        drop_k = int(drop_t * (sr / HOP))
        if drop_k < win:
            continue
        # Look back 8s for monotonic rise into the drop
        start_k = max(0, drop_k - 80)  # 8s back
        onset_slope = onset_smooth[drop_k] - onset_smooth[start_k]
        if onset_slope > 0:
            build_t = times[start_k]
            build_dur = drop_t - build_t
            raw_builds.append({
                "t": float(build_t), "type": "build",
                "duration": float(build_dur),
                "intensity": float(min(1.0, abs(onset_slope) / (onset_smooth.std() + 1e-6))),
                "confidence": 0.6,
            })
    events.extend(raw_builds)

    # ---- Peak: sustained broadband loudness (>= 6s above 70th percentile) ----
    broad_p70 = float(np.percentile(broad_db, 70))
    loud = broad_db > broad_p70
    i = 0
    while i < n:
        if loud[i]:
            j = i
            while j < n and loud[j]:
                j += 1
            dur = times[j - 1] - times[i] if j > i else 0
            if dur >= 6.0:
                events.append({
                    "t": float(times[i]), "type": "peak",
                    "duration": float(dur),
                    "intensity": float(min(1.0, (broad_db[i:j].mean() - broad_p70) / 6.0 + 0.5)),
                    "confidence": 0.6,
                })
            i = j
        else:
            i += 1

    events.sort(key=lambda e: e["t"])

    beats_list = [{"t": float(b), "confidence": 0.7} for b in beats]

    meta = {
        "duration": float(times[-1]) if len(times) else 0,
        "tempo": tempo,
        "n_beats": len(beats_list),
        "sub_p30_db": sub_p30,
        "broad_p80_db": broad_p80,
    }
    return events, beats_list, meta


def main() -> int:
    p = argparse.ArgumentParser(description="Multi-feature DJ-set event detection")
    p.add_argument("audio", type=Path, help="master audio file (WAV/FLAC/MP3)")
    p.add_argument("--out-dir", type=Path, required=True, help="project dir")
    p.add_argument("--start", type=float, default=0.0, help="trim master start (s)")
    p.add_argument("--end", type=float, default=None, help="trim master end (s)")
    p.add_argument("--max-drops", type=int, default=25)
    p.add_argument("--max-breakdowns", type=int, default=15)
    p.add_argument("--start-bpm", type=float, default=128.0,
                   help="tempo hint for beat tracker (prevents half-time lock)")
    args = p.parse_args()

    print(f"Loading {args.audio}...", file=sys.stderr)
    duration = (args.end - args.start) if args.end else None
    y, sr = librosa.load(str(args.audio), sr=SR, mono=True,
                         offset=args.start, duration=duration)
    print(f"Loaded {len(y)/sr:.1f}s @ {sr}Hz", file=sys.stderr)

    print("Detecting events...", file=sys.stderr)
    events, beats, meta = detect_events(
        y, sr,
        max_drops=args.max_drops,
        max_breakdowns=args.max_breakdowns,
        start_bpm=args.start_bpm,
    )

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "events.json", "w") as f:
        json.dump({"meta": meta, "events": events}, f, indent=2)
    with open(out / "beats.json", "w") as f:
        json.dump({"tempo": meta["tempo"], "beats": beats}, f, indent=2)

    by_type: dict[str, int] = {}
    for e in events:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print(f"events: {by_type} | beats: {len(beats)} | tempo: {meta['tempo']:.1f} BPM",
          file=sys.stderr)
    print(f"wrote {out/'events.json'} and {out/'beats.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
