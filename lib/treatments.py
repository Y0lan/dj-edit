"""treatments.py — visual treatments applied at drops and hooks.

Each treatment returns an ffmpeg filter chain fragment to apply to a clip.
Treatments preserve the clip's EDL duration — they MUST NOT extend or shrink it.
Otherwise the concat output drifts (audio runs ahead of video).
"""
from __future__ import annotations


def flash_freeze(width: int, height: int, fps: int,
                 zoom_factor: float = 1.18) -> str:
    """Zoom-punch on the drop impact frame.

    Applies a continuous zoom over the clip's duration ending at zoom_factor.
    Does NOT use tpad (which would extend duration). Duration-preserving.
    Uses `ot` (output time) — `t` is not valid in zoompan expressions.
    """
    return (
        f"zoompan=z='min(1+(({zoom_factor}-1)*ot/iw)*iw,{zoom_factor})':"
        f"d=1:s={width}x{height}:fps={fps}"
    )


def speed_ramp_into_drop(slowmo_factor: float = 0.5,
                         ramp_through: float = 0.7) -> str:
    """Slow into the drop, then snap to normal speed.

    Note: EXTENDS clip duration. Output duration = input_dur * (ramp_through * (1/slowmo - 1) + 1).
    EDL builder must allocate extra time when scheduling, OR caller must trim.
    Not yet wired into build-edl.py — kept for future use.
    """
    if slowmo_factor <= 0 or slowmo_factor >= 1.0:
        # No-op for invalid slowmo; just preserve PTS
        return "setpts=PTS-STARTPTS"
    slowmo = 1.0 / slowmo_factor  # 0.5 → 2x time on the slow portion
    offset = ramp_through * (slowmo - 1.0)  # PTS continuity at the boundary
    return (
        f"setpts='if(lt(T,{ramp_through}),PTS*{slowmo:.4f},"
        f"PTS+{offset:.4f})'"
    )


def triple_punch(width: int, height: int, fps: int,
                 amplitude: float = 0.12, freq_hz: float = 6.0) -> str:
    """Stuttering zoom-in pulses — visual stutter at a drop.

    Uses `ot` (output time) — `t` is not valid in zoompan expressions.
    Three pulses over the clip if freq_hz=6 and clip duration ~0.5s.
    """
    return (
        f"zoompan=z='1.0+{amplitude}*abs(sin(2*PI*ot*{freq_hz}))':"
        f"d=1:s={width}x{height}:fps={fps}"
    )


def color_pop(saturation: float = 1.4, contrast: float = 1.15) -> str:
    """Briefly boost saturation + contrast for impact moments."""
    return f"eq=saturation={saturation}:contrast={contrast}"


def whip_blur(blur_strength: float = 12.0) -> str:
    """Motion blur for whip transitions."""
    return f"gblur=sigma={blur_strength}"


TREATMENTS = {
    "flash_freeze": flash_freeze,
    "speed_ramp_into_drop": speed_ramp_into_drop,
    "triple_punch": triple_punch,
    "color_pop": color_pop,
    "whip_blur": whip_blur,
}


def apply(name: str, **kwargs) -> str:
    fn = TREATMENTS.get(name)
    if not fn:
        return ""
    return fn(**kwargs)
