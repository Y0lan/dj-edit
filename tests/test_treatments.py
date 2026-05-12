"""Unit tests for lib/treatments.py — visual treatment filter generators."""
import math
import re

from lib import treatments


class TestFlashFreeze:
    def test_uses_output_time_not_t(self):
        """zoompan accepts `ot`/`it`/`in`/`on`, NOT bare `t`. Using `t` makes
        ffmpeg fail at filter-init time with 'Undefined constant'."""
        out = treatments.flash_freeze(1080, 1920, 25)
        assert "ot" in out
        # No bare t allowed (word boundary).
        assert not re.search(r"\bt\b", out), f"flash_freeze uses bare t: {out}"

    def test_no_tpad(self):
        """flash_freeze must NOT extend the clip duration. The old impl used
        tpad=stop_duration=0.27 which made every flash_freeze clip 0.27s
        longer than its EDL allocation -> concat output drift."""
        out = treatments.flash_freeze(1080, 1920, 25)
        assert "tpad" not in out, f"flash_freeze still uses tpad: {out}"

    def test_includes_target_dimensions(self):
        out = treatments.flash_freeze(1080, 1920, 25)
        assert "1080x1920" in out

    def test_includes_target_fps(self):
        out = treatments.flash_freeze(720, 1280, 60)
        assert "fps=60" in out


class TestSpeedRamp:
    def test_default_args_no_division_by_zero(self):
        """speed_ramp_into_drop(0.5) used to compute 1.0/(2.0 - 1.0/0.5) = 1/0
        and emit 'inf'. Result was a broken setpts expression."""
        out = treatments.speed_ramp_into_drop(0.5)
        assert "inf" not in out.lower()
        assert "nan" not in out.lower()

    def test_edge_case_zero_slowmo(self):
        """slowmo=0 would mean 1/0 in the slow part; should not crash."""
        out = treatments.speed_ramp_into_drop(0.0)
        # Should return a valid no-op or guarded expression
        assert "inf" not in out.lower()
        assert "nan" not in out.lower()

    def test_edge_case_full_speed(self):
        out = treatments.speed_ramp_into_drop(1.0)
        assert "inf" not in out.lower()

    def test_valid_setpts_expression(self):
        """The output must start with `setpts=` and be parseable as a filter arg."""
        out = treatments.speed_ramp_into_drop(0.5)
        assert out.startswith("setpts=")


class TestTriplePunch:
    def test_uses_output_time(self):
        out = treatments.triple_punch(1080, 1920, 25)
        assert "ot" in out
        assert not re.search(r"\bt\b", out), f"triple_punch uses bare t: {out}"

    def test_dimensions_in_output(self):
        out = treatments.triple_punch(1920, 1080, 30)
        assert "1920x1080" in out
        assert "fps=30" in out


class TestColorPop:
    def test_eq_filter(self):
        out = treatments.color_pop(saturation=1.4, contrast=1.15)
        assert "eq=" in out
        assert "saturation=1.4" in out
        assert "contrast=1.15" in out


class TestWhipBlur:
    def test_gblur(self):
        out = treatments.whip_blur(blur_strength=12.0)
        assert "gblur=" in out
        assert "sigma=12" in out


class TestApply:
    def test_unknown_returns_empty(self):
        assert treatments.apply("nonsense") == ""

    def test_known_dispatches(self):
        out = treatments.apply("color_pop", saturation=1.5, contrast=1.2)
        assert "saturation=1.5" in out
