"""Guard tests for shipped sendcmd preset files.

Codex flagged in v0.2 round 1: the original preset .cmd files were not
regenerated after the lib.moves rewrite and still used the old `TI`
(normalized progress) syntax. This test makes that class of regression
loud — if anyone adds a preset that uses TI, pow(), or commas in
expressions, CI fails.
"""
import re
from pathlib import Path

import pytest


PRESET_DIR = Path(__file__).resolve().parent.parent / "presets" / "moves"


def _all_presets() -> list[Path]:
    return sorted(PRESET_DIR.glob("*.cmd"))


def _expressions(text: str) -> list[str]:
    """Extract just the expression portion of each command line."""
    out = []
    for line in text.strip().split("\n"):
        m = re.match(r"[\d.]+-[\d.]+ \[expr\] v360@mv \S+ (.+);", line.strip())
        if m:
            out.append(m.group(1))
    return out


@pytest.mark.parametrize("preset", _all_presets(), ids=lambda p: p.name)
class TestShippedPresetHygiene:
    def test_no_TI_in_expressions(self, preset: Path):
        """TI is normalized [0,1]; we use T (seconds) for all duration-scaled math."""
        text = preset.read_text()
        for expr in _expressions(text):
            assert not re.search(r"\bTI\b", expr), \
                f"{preset.name}: uses TI in expression: {expr}"

    def test_no_pow_in_expressions(self, preset: Path):
        """pow(x, n) has a comma → breaks sendcmd parsing. Use x*x or x*x*x."""
        text = preset.read_text()
        for expr in _expressions(text):
            assert "pow(" not in expr, \
                f"{preset.name}: uses pow() in expression: {expr}"

    def test_no_if_in_expressions(self, preset: Path):
        """if(lt(...), a, b) has commas → breaks sendcmd parsing. Use multi-line cmds."""
        text = preset.read_text()
        for expr in _expressions(text):
            assert "if(" not in expr, \
                f"{preset.name}: uses if() in expression: {expr}"

    def test_no_commas_in_expressions(self, preset: Path):
        """Commas inside sendcmd argument values break the parser (verified in
        ffmpeg n8.1.1). Multi-line time-windowed commands are the workaround."""
        text = preset.read_text()
        for expr in _expressions(text):
            assert "," not in expr, \
                f"{preset.name}: comma inside expression: {expr}"

    def test_parseable_command_lines(self, preset: Path):
        """Every non-empty line must match `<t0>-<t1> [expr] v360@mv <param> <expr>;`."""
        for raw in preset.read_text().strip().split("\n"):
            line = raw.strip()
            if not line:
                continue
            assert re.match(
                r"^\d+\.?\d*-\d+\.?\d* \[expr\] v360@mv (yaw|pitch|roll|h_fov|v_fov) .+;$",
                line,
            ), f"{preset.name}: malformed command line: {line}"
