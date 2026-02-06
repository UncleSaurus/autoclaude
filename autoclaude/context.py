"""Context discovery and loading for AutoClaude.

Automatically discovers and loads project context files (AGENTS.md, PROJECT_STATUS.md, etc.)
to give the agent awareness of the codebase it's working in.
"""

import re
from pathlib import Path

# Context files to auto-discover, in loading order.
# Each tuple is (filename, description for inventory).
CONTEXT_FILES = [
    ("AGENTS.md", "Agent configuration and project conventions"),
    ("CLAUDE.md", "AI assistant instructions"),
    ("PROJECT_STATUS.md", "Current tasks, known issues, and progress"),
    ("README.md", "Project overview and setup"),
]

MAX_FILE_SIZE = 50_000  # 50KB — truncate files larger than this


def discover_context_files(root: str | Path) -> list[tuple[Path, str]]:
    """Discover context files in a directory.

    Args:
        root: Directory to search for context files.

    Returns:
        List of (path, description) tuples for files that exist.
    """
    root = Path(root)
    found = []

    for filename, description in CONTEXT_FILES:
        path = root / filename
        if path.is_file():
            found.append((path, description))

    return found


def load_context(root: str | Path) -> str:
    """Load and format all context files into a prompt section.

    Args:
        root: Directory to search for context files.

    Returns:
        Formatted context string ready to prepend to agent prompts.
        Empty string if no context files found.
    """
    files = discover_context_files(root)
    if not files:
        return ""

    sections = []

    # File inventory header
    inventory_lines = ["# Project Context", "", "## Loaded Context Files", ""]
    for path, description in files:
        size = path.stat().st_size
        inventory_lines.append(f"- `{path.name}` ({size:,} bytes) — {description}")
    inventory_lines.append("")
    inventory_lines.append(
        "**Note**: These files are preloaded into context. "
        "Do not re-read them unless you need to verify specific content or check for updates."
    )
    inventory_lines.append("")
    sections.append("\n".join(inventory_lines))

    # File contents
    for path, _description in files:
        content = _read_and_sanitize(path)
        sections.append(f"## {path.name}\n\n{content}")

    return "\n---\n\n".join(sections) + "\n"


def load_progress_context(root: str | Path, max_entries: int = 5) -> str:
    """Load recent progress entries for iteration context.

    Args:
        root: Project root directory.
        max_entries: Maximum number of recent entries to include.

    Returns:
        Formatted progress context string, or empty string if no progress file.
    """
    progress_path = Path(root) / ".autoclaude" / "progress.md"
    if not progress_path.is_file():
        return ""

    content = _read_and_sanitize(progress_path)
    if not content.strip():
        return ""

    # Extract recent entries (each starts with "## Run")
    entries = re.split(r'(?=^## Run )', content, flags=re.MULTILINE)
    entries = [e.strip() for e in entries if e.strip().startswith("## Run")]

    if not entries:
        return ""

    recent = entries[-max_entries:]

    lines = [
        "# Recent AutoClaude Progress",
        "",
        f"Showing {len(recent)} most recent run(s):",
        "",
    ]
    lines.extend(recent)

    return "\n".join(lines) + "\n"


def _read_and_sanitize(path: Path) -> str:
    """Read a file and sanitize control characters."""
    content = path.read_text(encoding="utf-8", errors="replace")

    # Remove control characters except tab, newline, carriage return
    content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', content)

    if len(content) > MAX_FILE_SIZE:
        content = content[:MAX_FILE_SIZE] + f"\n\n... (truncated at {MAX_FILE_SIZE:,} bytes)"

    return content
