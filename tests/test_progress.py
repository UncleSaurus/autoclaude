"""Tests for progress tracking."""

import tempfile
from pathlib import Path

from autoclaude.progress import append_run, extract_learnings, get_progress_path, is_blocked, is_complete


def test_get_progress_path():
    path = get_progress_path("/my/project")
    assert str(path) == "/my/project/.autoclaude/progress.md"


def test_append_run_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        append_run(tmpdir, issue_number=42, title="Fix bug", status="COMPLETED")

        progress_path = get_progress_path(tmpdir)
        assert progress_path.exists()

        content = progress_path.read_text()
        assert "AutoClaude Progress Log" in content
        assert "Issue #42" in content
        assert "Fix bug" in content
        assert "COMPLETED" in content


def test_append_run_with_learnings():
    with tempfile.TemporaryDirectory() as tmpdir:
        append_run(
            tmpdir,
            issue_number=1,
            title="Test",
            status="COMPLETED",
            learnings=["Config is in config.toml", "Tests use pytest"],
        )

        content = get_progress_path(tmpdir).read_text()
        assert "config.toml" in content
        assert "pytest" in content


def test_append_run_appends():
    with tempfile.TemporaryDirectory() as tmpdir:
        append_run(tmpdir, issue_number=1, title="First", status="COMPLETED")
        append_run(tmpdir, issue_number=2, title="Second", status="BLOCKED")

        content = get_progress_path(tmpdir).read_text()
        assert "First" in content
        assert "Second" in content
        assert "Issue #1" in content
        assert "Issue #2" in content


def test_append_run_with_iteration():
    with tempfile.TemporaryDirectory() as tmpdir:
        append_run(tmpdir, issue_number=1, title="Complex", status="IN_PROGRESS", iteration=3)

        content = get_progress_path(tmpdir).read_text()
        assert "iteration 3" in content


def test_extract_learnings():
    output = """
I found the config file.
LEARNED: The config lives at config/settings.toml
Continuing work...
LEARNED: Tests require DATABASE_URL to be set
Done.
"""
    learnings = extract_learnings(output)
    assert len(learnings) == 2
    assert "config/settings.toml" in learnings[0]
    assert "DATABASE_URL" in learnings[1]


def test_extract_learnings_empty():
    assert extract_learnings("No learnings here") == []


def test_is_complete():
    assert is_complete("some output\nAUTOCLAUDE_COMPLETE\nmore output")
    assert not is_complete("some output without signal")


def test_is_blocked():
    assert is_blocked("AUTOCLAUDE_BLOCKED: need more info")
    assert not is_blocked("just regular output")
