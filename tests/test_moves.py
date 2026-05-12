"""Unit tests for lib/moves.py — sendcmd .cmd generators for v360@mv."""
import pytest

from lib import moves


class TestOrbit:
    def test_emits_yaw_with_TI(self):
        """orbit should drive yaw via TI (interval time)."""
        cmd = moves.orbit(duration=8.0, yaw_speed=45.0)
        assert "v360@mv yaw" in cmd
        assert "TI" in cmd

    def test_emits_three_lines(self):
        """orbit sets yaw, pitch, and h_fov."""
        cmd = moves.orbit(duration=8.0)
        lines = cmd.strip().split("\n")
        assert len(lines) == 3

    def test_uses_expr_flag(self):
        """Continuous expression eval requires [expr] flag."""
        cmd = moves.orbit(duration=8.0)
        assert "[expr]" in cmd


class TestTiltReveal:
    def test_pitch_interpolates(self):
        cmd = moves.tilt_reveal(duration=4.0, pitch_start=-60, pitch_end=0)
        assert "pitch" in cmd
        # Should compute a rate; (0 - (-60))/4 = 15
        assert "15.0000" in cmd or "15.0" in cmd


class TestDollyZoom:
    def test_h_fov_interpolates(self):
        cmd = moves.dolly_zoom(duration=4.0, fov_start=90, fov_end=55)
        assert "h_fov" in cmd
        # rate = (55 - 90) / 4 = -8.75
        assert "-8.75" in cmd


class TestWhipPan:
    def test_fast_yaw(self):
        cmd = moves.whip_pan(duration=0.4, yaw_speed=900.0)
        assert "v360@mv yaw" in cmd
        assert "900" in cmd
        assert "TI" in cmd


class TestCrowdReveal:
    def test_yaw_and_fov(self):
        cmd = moves.crowd_reveal(duration=6.0)
        assert "v360@mv yaw" in cmd
        assert "v360@mv h_fov" in cmd


class TestGenerate:
    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown move"):
            moves.generate("not_a_move", 1.0)

    def test_orbit_dispatches(self):
        cmd = moves.generate("orbit", duration=8.0)
        assert "yaw" in cmd

    def test_orbit_with_params(self):
        cmd = moves.generate("orbit", duration=8.0, params={"yaw_speed": 90.0})
        assert "90" in cmd


class TestFilterChain:
    def test_includes_sendcmd_and_v360(self):
        chain = moves.filter_chain("/path/to/cmd.txt", 1080, 1920)
        assert "sendcmd=f=" in chain
        assert "v360@mv=input=e:output=flat" in chain
        assert "w=1080" in chain
        assert "h=1920" in chain
