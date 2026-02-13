"""Configuration for AutoClaude."""

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AutoClaudeConfig:
    """Configuration for AutoClaude issue processing."""

    # Platform selection
    platform: str = "github"  # "github" or "azuredevops"

    # GitHub settings
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    repo: str = ""  # Format: "owner/repo"
    bot_assignee: str = field(
        default_factory=lambda: os.environ.get("GITHUB_BOT_ASSIGNEE", "claude-bot")
    )
    human_reviewer: str = field(
        default_factory=lambda: os.environ.get("GITHUB_HUMAN_REVIEWER", "")
    )

    # Azure DevOps settings
    ado_org: str = field(default_factory=lambda: os.environ.get("ADO_ORG", ""))
    ado_project: str = field(default_factory=lambda: os.environ.get("ADO_PROJECT", ""))
    ado_repo: str = field(default_factory=lambda: os.environ.get("ADO_REPO", ""))

    # Claude Agent SDK settings
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    model: str = "claude-sonnet-4-5-20250929"
    max_turns: int = 50
    cli_path: Optional[str] = None  # Path to claude CLI binary (None = auto-detect)

    # Processing settings
    max_ci_retries: int = 3
    ci_poll_interval: int = 30
    ci_timeout: int = 600

    # Labels/tags for agent coordination
    label_analyzing: str = "agent-analyzing"
    label_clarifying: str = "agent-clarifying"
    label_in_progress: str = "agent-claimed"
    label_blocked: str = "agent-blocked"
    label_failed: str = "agent-failed"
    label_completed: str = "agent-complete"

    # Behavior
    dry_run: bool = False
    post_plan_comment: bool = True
    create_draft_pr: bool = False
    skip_pr: bool = False  # --no-pr: skip PR creation
    skip_clarification: bool = False
    clarification_timeout: int = 86400

    # Specific issue to process (None = process all assigned)
    issue_number: Optional[int] = None

    # Repository location
    repo_dir: Optional[str] = None  # Local filesystem path to target repo (default: cwd)

    # Git remote settings
    git_remote: str = "origin"  # Git remote for fetch/push (default: origin)
    base_branch: str = "main"  # Base branch name (default: main)

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

    # DAG settings
    max_parallel: int = 4  # Max parallel tickets per wave
    test_command: Optional[str] = None  # Post-merge validation command

    # Output settings
    verbose: bool = False  # Stream agent actions to terminal in real-time

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if self.platform == "github":
            if not self.github_token:
                errors.append("GITHUB_TOKEN environment variable is required")
            if not self.repo:
                errors.append("Repository (--repo) is required")
            if "/" not in self.repo and self.repo:
                errors.append("Repository must be in format 'owner/repo'")
        elif self.platform == "azuredevops":
            if not self.ado_org:
                errors.append("ADO organization (--ado-org or ADO_ORG) is required")
            if not self.ado_project:
                errors.append("ADO project (--ado-project or ADO_PROJECT) is required")
            if not self.ado_repo:
                errors.append("ADO repository (--ado-repo or ADO_REPO) is required")
        else:
            errors.append(f"Unknown platform: {self.platform}. Use 'github' or 'azuredevops'")

        # ANTHROPIC_API_KEY is optional — Claude Agent SDK can use Claude Code's OAuth auth

        return errors

    @staticmethod
    def detect_repo_from_remote(remote: str = "origin", cwd: str | None = None) -> str:
        """Auto-detect owner/repo from git remote URL.

        Handles SSH and HTTPS formats:
          git@github.com:owner/repo.git -> owner/repo
          https://github.com/owner/repo.git -> owner/repo
          https://dev.azure.com/org/project/_git/repo -> repo
        """
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote],
                capture_output=True, text=True, check=True, cwd=cwd, timeout=5,
            )
            url = result.stdout.strip()
            # SSH: git@github.com:owner/repo.git
            ssh_match = re.match(r"git@[^:]+:(.+?)(?:\.git)?$", url)
            if ssh_match:
                return ssh_match.group(1)
            # HTTPS: https://github.com/owner/repo.git
            https_match = re.match(r"https?://[^/]+/(.+?)(?:\.git)?$", url)
            if https_match:
                return https_match.group(1)
        except Exception:
            pass
        return ""

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
