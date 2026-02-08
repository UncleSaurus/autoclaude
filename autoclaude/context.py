"""Context discovery and loading for AutoClaude.

Automatically discovers and loads project context files (AGENTS.md, PROJECT_STATUS.md, etc.)
to give the agent awareness of the codebase it's working in.

When a context file references another file by absolute path (e.g.,
``See /path/to/AGENTS.md``), that file is resolved and included automatically.
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

# Pattern to find absolute paths to .md files referenced in context files.
_FILE_REF_PATTERN = re.compile(r'(/[\w./-]+\.md)\b')


def _resolve_file_references(content: str, seen: set[str]) -> list[tuple[Path, str]]:
    """Scan content for absolute .md file paths and return any that exist on disk.

    Args:
        content: Text content to scan for file references.
        seen: Set of already-loaded resolved paths (mutated to include new finds).

    Returns:
        List of (path, description) tuples for referenced files not already loaded.
    """
    found = []
    for match in _FILE_REF_PATTERN.finditer(content):
        ref_path = Path(match.group(1))
        resolved = str(ref_path.resolve())
        if resolved not in seen and ref_path.is_file():
            seen.add(resolved)
            found.append((ref_path, f"Referenced from context ({ref_path.name})"))
    return found


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

    Discovered files are read and scanned for absolute .md file references.
    Referenced files are resolved and included so that linked standards
    (e.g., a global AGENTS.md) are always injected into context.

    Args:
        root: Directory to search for context files.

    Returns:
        Formatted context string ready for system prompt injection.
        Empty string if no context files found.
    """
    project_files = discover_context_files(root)
    if not project_files:
        return ""

    # Track resolved paths to avoid duplicates.
    seen: set[str] = {str(p.resolve()) for p, _ in project_files}

    # Read project files and resolve any referenced files.
    referenced_files: list[tuple[Path, str]] = []
    file_contents: dict[str, str] = {}

    for path, _desc in project_files:
        content = _read_and_sanitize(path)
        file_contents[str(path.resolve())] = content
        referenced_files.extend(_resolve_file_references(content, seen))

    # Read referenced files (and resolve their references too, one level deep).
    for path, _desc in referenced_files:
        content = _read_and_sanitize(path)
        file_contents[str(path.resolve())] = content

    # Final ordered list: referenced (global) files first, then project files.
    all_files = referenced_files + project_files

    # Build inventory header
    sections = []
    inventory_lines = ["# Project Context", "", "## Loaded Context Files", ""]
    for path, description in all_files:
        size = path.stat().st_size
        inventory_lines.append(f"- `{path}` ({size:,} bytes) — {description}")
    inventory_lines.append("")
    inventory_lines.append(
        "**Note**: These files are preloaded into context. "
        "Do not re-read them unless you need to verify specific content or check for updates."
    )
    inventory_lines.append("")
    sections.append("\n".join(inventory_lines))

    # File contents — referenced (global) files first
    for path, _description in all_files:
        content = file_contents[str(path.resolve())]
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
