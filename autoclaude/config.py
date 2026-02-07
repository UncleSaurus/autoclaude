"""Configuration for AutoClaude."""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AutoClaudeConfig:
    """Configuration for AutoClaude issue processing."""

    # GitHub settings
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    repo: str = ""  # Format: "owner/repo"
    bot_assignee: str = field(
        default_factory=lambda: os.environ.get("GITHUB_BOT_ASSIGNEE", "claude-bot")
    )
    human_reviewer: str = field(
        default_factory=lambda: os.environ.get("GITHUB_HUMAN_REVIEWER", "")
    )

    # Claude Agent SDK settings
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    model: str = "claude-sonnet-4-5-20250929"
    max_turns: int = 50

    # Processing settings
    max_ci_retries: int = 3
    ci_poll_interval: int = 30
    ci_timeout: int = 600

    # Labels for agent coordination
    label_analyzing: str = "agent-analyzing"
    label_clarifying: str = "agent-clarifying"
    label_in_progress: str = "agent-claimed"
    label_blocked: str = "agent-blocked"
    label_failed: str = "agent-blocked"
    label_completed: str = "agent-complete"

    # Behavior
    dry_run: bool = False
    post_plan_comment: bool = True
    create_draft_pr: bool = False
    skip_clarification: bool = False
    clarification_timeout: int = 86400

    # Specific issue to process (None = process all assigned)
    issue_number: Optional[int] = None

    # Worktree settings for isolation
    use_worktree: bool = False
    worktree_base_path: str = "../"
    worktree_path: Optional[str] = None

    # Context loading
    context_dir: Optional[str] = None  # Override context discovery root (default: cwd)
    no_context: bool = False  # Skip context loading entirely

    # Iteration settings
    max_iterations: int = 1  # 1 = single pass (default), >1 = iterative

    # Quality gate settings
    quality_checks: list[str] = field(default_factory=list)  # CLI-specified check commands
    max_quality_retries: int = 2  # Max attempts to fix quality failures

    # Output settings
    verbose: bool = False  # Stream agent actions to terminal in real-time

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.github_token:
            errors.append("GITHUB_TOKEN environment variable is required")

        if not self.repo:
            errors.append("Repository (--repo) is required")

        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY environment variable is required")

        if "/" not in self.repo and self.repo:
            errors.append("Repository must be in format 'owner/repo'")

        return errors

    @property
    def repo_owner(self) -> str:
        if "/" in self.repo:
            return self.repo.split("/")[0]
        return ""

    @property
    def repo_name(self) -> str:
        if "/" in self.repo:
            return self.repo.split("/")[1]
        return ""
