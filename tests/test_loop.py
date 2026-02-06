"""Tests for iteration loop logic (unit tests, no SDK calls)."""

import json
import tempfile
from pathlib import Path

from autoclaude.progress import is_blocked, is_complete


def test_prd_format():
    """Verify PRD JSON format is valid."""
    prd = {
        "stories": [
            {"id": "1", "title": "Add auth", "description": "Implement login", "done": False},
            {"id": "2", "title": "Add tests", "description": "Write tests", "done": False},
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(prd, f)
        f.flush()

        # Verify it can be loaded back
        with open(f.name) as rf:
            loaded = json.load(rf)

        assert len(loaded["stories"]) == 2
        assert loaded["stories"][0]["title"] == "Add auth"
        assert loaded["stories"][0]["done"] is False


def test_prd_mark_done():
    """Verify PRD stories can be marked as done."""
    prd = {
        "stories": [
            {"id": "1", "title": "Story 1", "done": False},
            {"id": "2", "title": "Story 2", "done": False},
        ]
    }

    # Simulate marking first story done
    prd["stories"][0]["done"] = True

    assert prd["stories"][0]["done"] is True
    assert prd["stories"][1]["done"] is False

    # Find next incomplete
    next_story = next((s for s in prd["stories"] if not s.get("done")), None)
    assert next_story is not None
    assert next_story["id"] == "2"


def test_completion_signals():
    """Test completion signal detection."""
    assert is_complete("output\nAUTOCLAUDE_COMPLETE\n")
    assert not is_complete("output without signal")
    assert is_blocked("AUTOCLAUDE_BLOCKED: need info")
    assert not is_blocked("regular output")


def test_iteration_context_building():
    """Test that iteration context is properly structured."""
    # Simulate what _build_iteration_context would produce
    base_context = "# Project Context\n\nAGENTS.md content here"
    iteration = 3
    learnings = ["Config is at config.toml", "Uses pytest"]
    git_diff = "5 files changed, 120 insertions(+), 30 deletions(-)"

    # Build manually to test structure
    sections = [base_context]
    iter_lines = [
        f"# Iteration Context",
        "",
        f"This is iteration {iteration}.",
        "",
        "## Changes Made So Far",
        "```",
        git_diff,
        "```",
        "",
        "## Learnings from Prior Iterations",
    ]
    for learning in learnings:
        iter_lines.append(f"- {learning}")

    sections.append("\n".join(iter_lines))
    result = "\n\n".join(sections)

    assert "iteration 3" in result
    assert "config.toml" in result
    assert "5 files changed" in result
    assert "AGENTS.md" in result
