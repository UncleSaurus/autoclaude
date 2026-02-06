"""Tests for context discovery and loading."""

import tempfile
from pathlib import Path

from autoclaude.context import discover_context_files, load_context, load_progress_context


def test_discover_no_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = discover_context_files(tmpdir)
        assert result == []


def test_discover_agents_md():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "AGENTS.md").write_text("# Agent config")
        result = discover_context_files(tmpdir)
        assert len(result) == 1
        assert result[0][0].name == "AGENTS.md"
        assert "conventions" in result[0][1].lower()


def test_discover_multiple_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "AGENTS.md").write_text("# Agents")
        (Path(tmpdir) / "README.md").write_text("# Readme")
        (Path(tmpdir) / "PROJECT_STATUS.md").write_text("# Status")
        result = discover_context_files(tmpdir)
        names = [p.name for p, _ in result]
        assert "AGENTS.md" in names
        assert "README.md" in names
        assert "PROJECT_STATUS.md" in names


def test_discover_preserves_order():
    """Files should be returned in the defined priority order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "README.md").write_text("# Readme")
        (Path(tmpdir) / "AGENTS.md").write_text("# Agents")
        result = discover_context_files(tmpdir)
        names = [p.name for p, _ in result]
        # AGENTS.md comes before README.md in the defined order
        assert names.index("AGENTS.md") < names.index("README.md")


def test_load_context_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = load_context(tmpdir)
        assert result == ""


def test_load_context_with_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "AGENTS.md").write_text("# My Project Agent\nDo things right.")
        result = load_context(tmpdir)
        assert "AGENTS.md" in result
        assert "My Project Agent" in result
        assert "preloaded" in result.lower()


def test_load_context_inventory_header():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "AGENTS.md").write_text("content here")
        result = load_context(tmpdir)
        assert "Loaded Context Files" in result
        assert "bytes" in result


def test_load_context_sanitizes_control_chars():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "AGENTS.md").write_text("hello\x00world\x07test")
        result = load_context(tmpdir)
        assert "\x00" not in result
        assert "\x07" not in result
        assert "helloworld" in result


def test_load_progress_context_no_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = load_progress_context(tmpdir)
        assert result == ""


def test_load_progress_context_with_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        progress_dir = Path(tmpdir) / ".autoclaude"
        progress_dir.mkdir()
        (progress_dir / "progress.md").write_text(
            "# AutoClaude Progress Log\n\n"
            "## Run 2026-01-01T12:00:00 — Issue #1: Test\n"
            "- Status: COMPLETED\n\n"
            "## Run 2026-01-02T12:00:00 — Issue #2: Another\n"
            "- Status: BLOCKED\n"
        )
        result = load_progress_context(tmpdir)
        assert "Issue #1" in result
        assert "Issue #2" in result
        assert "Recent AutoClaude Progress" in result
