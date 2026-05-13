"""Unit tests for lib/moves.py — sendcmd .cmd generators for v360@mv.

These tests encode the CRITICAL invariants codex identified:
  - Use `T` (seconds since command start) NOT bare `TI` for duration-scaled math.
  - Avoid commas inside expressions (pow(x,2) → x*x; nested if → multi-line cmds).
"""
import re

import pytest

from lib import moves


# ───────────────────────────── Invariants ────────────────────────────────


def _expressions(cmd_text: str) -> list[str]:
    """Extract the expression strings from a generated .cmd block."""
    out = []
    for line in cmd_text.strip().split("\n"):
        # Format: "<t0>-<t1> [expr] v360@mv <param> <expression>;"
        m = re.match(r"[\d.]+-[\d.]+ \[expr\] v360@mv \S+ (.+);", line)
        if m:
            out.append(m.group(1))
    return out


def assert_no_pow(cmd_text: str) -> None:
    """pow(x, 2) has a comma → breaks sendcmd parsing. Use x*x instead."""
    for expr in _expressions(cmd_text):
        assert "pow(" not in expr, f"expression uses pow(): {expr}"


def assert_no_if_with_commas(cmd_text: str) -> None:
    """Nested if(lt(x,a),b,if(lt(x,c),d,e)) breaks sendcmd. Use multi-line cmds instead."""
    for expr in _expressions(cmd_text):
        # Allow if() only if its argument list doesn't span commas.
        # We're strict: simply ban if( in any expression for safety.
        assert "if(" not in expr, f"expression uses if(): {expr}"


def assert_uses_T_for_time(cmd_text: str) -> None:
    """T is absolute seconds. TI is normalized [0,1]. For duration-scaled math
    we use T because the semantics are unambiguous."""
    text = " ".join(_expressions(cmd_text))
    # At least one expression should reference T (the time variable). If a
    # function is purely static it's fine to skip — checked per-test.
    if "T" not in text:
        return
    # Reject hidden TI misuse: we should NOT see math like "X*TI" because that
    # would be normalized progress, easily mistaken for seconds.
    # (Note: bare TI is allowed in static expressions, but we don't use it.)
    assert "*TI" not in text and "TI*" not in text, \
        f"expression uses TI for math: {text}"


# ───────────────────────── Tier A: eased primitives ─────────────────────────


class TestOrbit:
    def test_emits_all_three_params(self):
        cmd = moves.orbit(duration=8.0)
        assert "v360@mv yaw" in cmd
        assert "v360@mv pitch" in cmd
        assert "v360@mv h_fov" in cmd

    def test_eased_uses_cos(self):
        cmd = moves.orbit(duration=8.0, ease=True)
        assert "cos" in cmd

    def test_linear_uses_yaw_speed_times_T(self):
        cmd = moves.orbit(duration=8.0, yaw_speed=45.0, ease=False)
        # Linear: yaw = 45*T (deg/sec * seconds = deg)
        assert "45.0*T" in cmd or "45*T" in cmd

    def test_clean_invariants(self):
        cmd = moves.orbit(duration=8.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)


class TestTiltReveal:
    def test_clean_invariants(self):
        cmd = moves.tilt_reveal(duration=4.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)

    def test_includes_pitch_start_value(self):
        cmd = moves.tilt_reveal(duration=4.0, pitch_start=-60, pitch_end=0)
        assert "-60" in cmd


class TestDollyZoom:
    def test_clean_invariants(self):
        cmd = moves.dolly_zoom(duration=4.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)


class TestWhipPan:
    def test_fast_yaw(self):
        cmd = moves.whip_pan(duration=0.4, yaw_speed=900.0)
        assert "900" in cmd
        assert "*T" in cmd


class TestCrowdReveal:
    def test_clean_invariants(self):
        cmd = moves.crowd_reveal(duration=6.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)


class TestCounterMotion:
    def test_clean_invariants(self):
        cmd = moves.counter_motion(duration=8.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)

    def test_three_params(self):
        cmd = moves.counter_motion(duration=8.0)
        assert "yaw" in cmd and "pitch" in cmd and "h_fov" in cmd


class TestDollyWithDrift:
    def test_clean_invariants(self):
        cmd = moves.dolly_with_drift(duration=4.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)


# ─────────────────── Tier B: music-driven primitives ────────────────────────


class TestBpmSyncOrbit:
    def test_yaw_speed_derived_from_bpm(self):
        # At 128 BPM, 4 bars = 4 * (60/128 * 4) = 7.5s. 360/7.5 = 48 deg/s
        cmd = moves.bpm_sync_orbit(duration=15.0, bpm=128.0, bars_per_rotation=4)
        assert "*T" in cmd
        assert "48" in cmd

    def test_handles_low_bpm(self):
        cmd = moves.bpm_sync_orbit(duration=10.0, bpm=0.001)
        # Should not crash; clamps to safe_bpm=30
        assert "yaw" in cmd

    def test_clean_invariants(self):
        cmd = moves.bpm_sync_orbit(duration=10.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)


class TestKickPulseFov:
    def test_uses_abs_sin(self):
        cmd = moves.kick_pulse_fov(duration=10.0, bpm=128.0)
        assert "abs(sin" in cmd

    def test_no_commas_in_abs_sin(self):
        # abs(sin(PI*T/x)) — only one arg, no comma inside abs() or sin()
        cmd = moves.kick_pulse_fov(duration=10.0)
        assert_no_if_with_commas(cmd)
        # abs(x) has no comma; sin(x) has no comma
        for expr in _expressions(cmd):
            assert "abs(sin(" in expr or "sin" not in expr


class TestDropImpact:
    def test_no_nested_if_uses_multiple_commands(self):
        """drop_impact MUST emit multiple time-windowed commands instead of
        if(lt(...), ...) — codex caught the nested-if comma bug."""
        cmd = moves.drop_impact(duration=0.8, target_yaw=90.0)
        assert_no_if_with_commas(cmd)
        # Expect multiple lines (one command per phase)
        line_count = len([l for l in cmd.strip().split("\n") if l.strip()])
        assert line_count >= 5, f"drop_impact should emit >=5 commands, got {line_count}"

    def test_target_yaw_appears(self):
        cmd = moves.drop_impact(duration=0.8, target_yaw=90.0)
        assert "90" in cmd

    def test_target_yaw_zero_allowed(self):
        # target_yaw=0 should produce a valid cmd; codex caught `or 60.0` bug.
        cmd = moves.drop_impact(duration=0.8, target_yaw=0.0)
        assert "yaw" in cmd

    def test_short_duration_falls_back(self):
        cmd = moves.drop_impact(duration=0.05, target_yaw=90.0)
        # Should be a single static hold, not 4 phases
        assert "v360@mv" in cmd

    def test_shortest_path_wraps_negative(self):
        """target_yaw=337.5 with start_yaw=0 should whip -22.5° (short way),
        not +337.5° (the long way around). Regression for codex finding."""
        cmd = moves.drop_impact(duration=0.8, target_yaw=337.5)
        # The whip-phase expression should multiply by -22.5, not 337.5
        # (the linear-interp coefficient is the shortest-path delta).
        # We can detect the negative delta by looking for "+-22.5" or "-22.5*"
        assert "-22.5" in cmd, \
            f"expected shortest-path delta -22.5, but cmd was:\n{cmd}"

    def test_shortest_path_explicit_start_yaw(self):
        """With start_yaw=350, target_yaw=10 → should whip +20° (not -340°)."""
        cmd = moves.drop_impact(duration=0.8, target_yaw=10.0, start_yaw=350.0)
        # delta = ((10 - 350 + 540) % 360) - 180 = (200 % 360) - 180 = 20
        assert "20.0" in cmd or "20*" in cmd, \
            f"expected shortest-path delta +20, but cmd was:\n{cmd}"

    def test_start_yaw_appears_in_phase1(self):
        """Phase 1 should hold start_yaw (not always 0)."""
        cmd = moves.drop_impact(duration=0.8, target_yaw=180.0, start_yaw=90.0)
        assert "90" in cmd  # start_yaw should appear in the cmd
        # First yaw line should be the constant start_yaw
        lines = [l for l in cmd.split("\n") if "yaw" in l and "pitch" not in l]
        assert lines[0].rstrip(";").endswith("90.0") or "90" in lines[0]


class TestBuildTension:
    def test_uses_t_times_t_not_pow(self):
        """Quadratic ease-in must be T*T, not pow(T,2) — comma breaks sendcmd."""
        cmd = moves.build_tension(duration=8.0)
        assert "T*T" in cmd
        assert_no_pow(cmd)

    def test_clean_invariants(self):
        cmd = moves.build_tension(duration=8.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)

    def test_zero_duration_safe(self):
        cmd = moves.build_tension(duration=0.0)
        assert "v360@mv" in cmd


class TestBreakdownDrift:
    def test_clean_invariants(self):
        cmd = moves.breakdown_drift(duration=12.0)
        assert_no_pow(cmd)
        assert_no_if_with_commas(cmd)
        assert_uses_T_for_time(cmd)


# ─────────────────────────── Dispatch + filter chain ────────────────────────


class TestGenerate:
    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown move"):
            moves.generate("not_a_move", 1.0)

    def test_orbit_dispatches(self):
        cmd = moves.generate("orbit", duration=8.0)
        assert "yaw" in cmd

    def test_drop_impact_dispatches(self):
        cmd = moves.generate("drop_impact", duration=0.8)
        assert "v360@mv" in cmd

    def test_all_new_moves_registered(self):
        expected = {
            "orbit", "tilt_reveal", "dolly_zoom", "whip_pan", "crowd_reveal",
            "counter_motion", "dolly_with_drift",
            "bpm_sync_orbit", "kick_pulse_fov", "drop_impact",
            "build_tension", "breakdown_drift",
        }
        assert set(moves.MOVES.keys()) >= expected

    def test_every_move_passes_invariants(self):
        """Smoke: every registered move's output respects no-pow + no-if-with-commas."""
        for name in moves.MOVES.keys():
            cmd = moves.generate(name, duration=2.0)
            assert_no_pow(cmd), f"{name} uses pow()"
            assert_no_if_with_commas(cmd), f"{name} uses if() with commas"


class TestFilterChain:
    def test_includes_sendcmd_and_v360(self):
        chain = moves.filter_chain("/path/to/cmd.txt", 1080, 1920)
        assert "sendcmd=f=" in chain
        assert "v360@mv=input=e:output=flat" in chain
        assert "w=1080" in chain
        assert "h=1920" in chain


# ─────────────────── Content-aware start_yaw (v0.3) ───────────────────


class TestStartYawParam:
    """The four moves that accept start_yaw should incorporate it into the
    yaw expression. Default start_yaw=0 must preserve existing behavior."""

    def test_orbit_with_start_yaw(self):
        cmd = moves.orbit(duration=4.0, start_yaw=180.0)
        # start_yaw should appear as a literal offset
        assert "180.0" in cmd
        # Should still be a valid sendcmd (no commas in expressions)
        assert_no_if_with_commas(cmd)
        assert_no_pow(cmd)

    def test_orbit_default_start_yaw_unchanged(self):
        # start_yaw=0 should be functionally equivalent to no-arg orbit
        default_cmd = moves.orbit(duration=4.0)
        zero_cmd = moves.orbit(duration=4.0, start_yaw=0.0)
        # Functional content should be the same (allowing whitespace tweaks)
        assert default_cmd == zero_cmd

    def test_crowd_reveal_with_start_yaw(self):
        cmd = moves.crowd_reveal(duration=6.0, start_yaw=90.0)
        assert "90.0" in cmd
        assert_no_if_with_commas(cmd)

    def test_bpm_sync_orbit_with_start_yaw(self):
        cmd = moves.bpm_sync_orbit(duration=10.0, bpm=128.0, start_yaw=270.0)
        assert "270.0" in cmd
        assert "*T" in cmd  # still BPM-driven

    def test_breakdown_drift_with_start_yaw(self):
        cmd = moves.breakdown_drift(duration=12.0, start_yaw=45.0)
        assert "45.0" in cmd
        # Should preserve the existing yaw_speed * T component
        assert "8.0*T" in cmd or "8*T" in cmd

    def test_zero_duration_with_start_yaw_doesnt_crash(self):
        cmd = moves.orbit(duration=0.0, start_yaw=180.0)
        assert "v360@mv" in cmd
        # Even at zero duration, start_yaw should be honored
        assert "180" in cmd
