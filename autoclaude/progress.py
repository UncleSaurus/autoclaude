"""Progress tracking for AutoClaude.

Maintains an append-only progress log at .autoclaude/progress.md that persists
learnings across runs. Each run appends an entry with timestamp, issue, status,
and captured learnings.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional


def get_progress_path(root: str | Path) -> Path:
    """Get the path to the progress file."""
    return Path(root) / ".autoclaude" / "progress.md"


def ensure_progress_dir(root: str | Path) -> Path:
    """Ensure the .autoclaude directory exists and return progress file path."""
    progress_path = get_progress_path(root)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    return progress_path


def append_run(
    root: str | Path,
    issue_number: int,
    title: str,
    status: str,
    branch: Optional[str] = None,
    pr_url: Optional[str] = None,
    learnings: Optional[list[str]] = None,
    iteration: Optional[int] = None,
) -> None:
    """Append a run entry to the progress log.

    Args:
        root: Project root directory.
        issue_number: GitHub issue number.
        title: Issue title.
        status: Final status (COMPLETED, BLOCKED, FAILED, etc.)
        branch: Branch name if created.
        pr_url: PR URL if created.
        learnings: List of learnings captured from agent output.
        iteration: Iteration number if running in multi-iteration mode.
    """
    progress_path = ensure_progress_dir(root)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    iteration_str = f" (iteration {iteration})" if iteration else ""

    lines = [
        f"## Run {timestamp} — Issue #{issue_number}: {title}{iteration_str}",
        f"- Status: {status}",
    ]

    if branch:
        lines.append(f"- Branch: {branch}")

    if pr_url:
        lines.append(f"- PR: {pr_url}")

    if learnings:
        lines.append("- Learnings:")
        for learning in learnings:
            lines.append(f"  - {learning}")

    lines.append("")

    entry = "\n".join(lines) + "\n"

    if progress_path.exists():
        with open(progress_path, "a") as f:
            f.write(entry)
    else:
        with open(progress_path, "w") as f:
            f.write("# AutoClaude Progress Log\n\n")
            f.write(entry)


def extract_learnings(agent_output: str) -> list[str]:
    """Extract LEARNED: markers from agent output.

    The agent is instructed to output `LEARNED: <insight>` when it discovers
    something useful about the codebase. These are captured into the progress log.
    """
    matches = re.findall(r'LEARNED:\s*(.+?)(?:\n|$)', agent_output, re.IGNORECASE)
    return [m.strip() for m in matches if m.strip()]


def extract_summary(agent_output: str) -> str:
    """Extract AUTOCLAUDE_SUMMARY from agent output for commit messages."""
    match = re.search(r'AUTOCLAUDE_SUMMARY:\s*(.+?)(?:\n|$)', agent_output, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def is_complete(agent_output: str) -> bool:
    """Check if the agent signaled completion."""
    return "AUTOCLAUDE_COMPLETE" in agent_output


def is_blocked(agent_output: str) -> bool:
    """Check if the agent signaled it's blocked."""
    return "AUTOCLAUDE_BLOCKED" in agent_output
