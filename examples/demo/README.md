# Demo fixture

A 30-second synthesized DJ-set-shaped audio + matching video, for testing the pipeline end-to-end with zero real footage.

## Files

- `demo_audio.wav` — 30s, 48 kHz, stereo PCM:
  - **0-10s**: low-volume ambient sine (kick clicks at 128 BPM)
  - **10-15s**: rising volume (the "build")
  - **15s**: the **drop** — sub-bass joins, volume jumps
  - **15-30s**: sustained peak with kick + sub
- `demo_video.mp4` — 30s, 1920x1080 @ 25 fps:
  - SMPTE color bars
  - Drawtext timer at bottom showing current time

## How it was generated

```bash
ffmpeg -y -f lavfi -i "sine=frequency=120:duration=30:sample_rate=48000,volume='if(lt(t,10),0.3,if(lt(t,15),0.3+0.4*((t-10)/5),0.7))'" \
       -f lavfi -i "aevalsrc='sin(2*PI*1500*t)*0.3*if(lt(mod(t,0.469),0.05),exp(-mod(t,0.469)*20),0)':duration=30:sample_rate=48000" \
       -f lavfi -i "aevalsrc='if(gt(t,15)*lt(t,30),sin(2*PI*60*t)*0.5,0)':duration=30:sample_rate=48000" \
       -filter_complex "[0:a][1:a][2:a]amix=inputs=3:duration=shortest:normalize=0[a]" \
       -map "[a]" -ac 2 -ar 48000 -c:a pcm_s16le demo_audio.wav

ffmpeg -y -f lavfi -i "smptebars=size=1920x1080:duration=30:rate=25" \
       -vf "drawtext=text='DJ-EDIT DEMO — t=%{pts\\:hms}':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.5:boxborderw=10" \
       -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p demo_video.mp4
```

## Usage

```bash
dj-edit init demo-project
dj-edit ingest demo-project --a7iii=examples/demo/demo_video.mp4 --audio=examples/demo/demo_audio.wav
dj-edit run demo-project
```

Expected: one detected drop at t≈15s, a short rendered set with the color-bar video cut to the synthesized drop track.
