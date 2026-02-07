"""Permission guard for autonomous agent execution.

Instead of bypassPermissions (which requires API key auth), this module
provides PreToolUse hooks that auto-approve safe operations and block
dangerous ones. Works with OAuth/Max plan authentication.

The orchestrator acts as the security gate — lemons to lemonade.
"""

import logging
import re
from typing import Any

from claude_agent_sdk.types import HookMatcher

logger = logging.getLogger(__name__)

# Dangerous bash patterns — blocked unconditionally
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # Destructive file operations
    (r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(/|~|\$HOME|\.\.|\.)\b", "rm targeting root, home, or parent dirs"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f", "recursive force delete"),
    (r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r", "recursive force delete"),
    (r"\bsudo\s+rm\b", "sudo rm"),
    (r":\s*>\s*/", "truncating files at root"),
    (r"\bmkfs\b", "filesystem format"),
    (r"\bdd\s+.*of=/dev/", "dd writing to device"),

    # Git destruction
    (r"\bgit\s+push\s+.*--force\b", "force push"),
    (r"\bgit\s+push\s+-f\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "git clean force"),
    (r"\bgit\s+checkout\s+\.\s*$", "git checkout . (discard all)"),

    # System damage
    (r"\bchmod\s+777\b", "chmod 777"),
    (r"\bchmod\s+-R\b", "recursive chmod"),
    (r"\bchown\s+-R\b", "recursive chown"),
    (r"\bsudo\b", "sudo usage"),
    (r"\bkill\s+-9\b", "kill -9"),
    (r"\bkillall\b", "killall"),
    (r"\bpkill\b", "pkill"),
    (r"\bshutdown\b", "shutdown"),
    (r"\breboot\b", "reboot"),

    # Data exfiltration
    (r"\bcurl\s+.*-X\s*POST\b", "curl POST (potential exfiltration)"),
    (r"\bcurl\s+.*--data\b", "curl with data (potential exfiltration)"),
    (r"\bwget\s+.*-O\s*-\s*\|", "wget pipe (potential exfiltration)"),

    # Database destruction
    (r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", "SQL DROP"),
    (r"\bTRUNCATE\s+TABLE\b", "SQL TRUNCATE"),
    (r"\bDELETE\s+FROM\s+\w+\s*;?\s*$", "DELETE without WHERE"),

    # Credential exposure
    (r"\bcat\s+.*\.(env|pem|key|secret)", "reading credential files"),
    (r"\bprintenv\b", "dumping environment"),
    (r"\benv\s*$", "dumping environment"),
    (r"\bset\s*$", "dumping shell variables"),
]

# Compile patterns once
_COMPILED_DANGEROUS = [(re.compile(p, re.IGNORECASE), reason) for p, reason in DANGEROUS_PATTERNS]

# Paths that should never be written to
PROTECTED_PATHS = [
    re.compile(r"^/(etc|usr|bin|sbin|var|System|Library)/"),
    re.compile(r"^/dev/"),
    re.compile(r"^/proc/"),
    re.compile(r"^~?/\.(ssh|gnupg|aws|kube)/"),
    re.compile(r"\.(env|pem|key|secret|credentials)$"),
]


def is_dangerous_command(command: str) -> tuple[bool, str]:
    """Check if a bash command matches dangerous patterns.

    Returns (is_dangerous, reason).
    """
    normalized = " ".join(command.split())

    for pattern, reason in _COMPILED_DANGEROUS:
        if pattern.search(normalized):
            return True, reason

    return False, ""


def is_protected_path(path: str) -> bool:
    """Check if a file path is in a protected location."""
    for pattern in PROTECTED_PATHS:
        if pattern.search(path):
            return True
    return False


# ---------------------------------------------------------------------------
# PreToolUse hook callbacks (work with OAuth, no API key required)
# ---------------------------------------------------------------------------

async def _bash_hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict:
    """PreToolUse hook for Bash commands — block dangerous patterns."""
    command = input_data.get("tool_input", {}).get("command", "")
    dangerous, reason = is_dangerous_command(command)

    if dangerous:
        logger.warning(f"BLOCKED bash: {reason} | cmd: {command[:200]}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Blocked by orchestrator: {reason}",
            }
        }

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


async def _write_hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict:
    """PreToolUse hook for Write/Edit — block writes to protected paths."""
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")

    if file_path and is_protected_path(file_path):
        logger.warning(f"BLOCKED write to protected path: {file_path}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Cannot write to protected path: {file_path}",
            }
        }

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


async def _allow_hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict:
    """PreToolUse hook that allows everything (for read-only tools)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def build_hooks() -> dict[str, list[HookMatcher]]:
    """Build the hooks dict for ClaudeAgentOptions.

    Returns hooks that auto-approve safe tools, validate writes,
    and block dangerous bash commands.
    """
    return {
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[_bash_hook]),
            HookMatcher(matcher="Write|Edit|NotebookEdit", hooks=[_write_hook]),
            # Allow everything else (Read, Glob, Grep, WebSearch, etc.)
            HookMatcher(matcher=None, hooks=[_allow_hook]),
        ],
    }
