"""GitHub API client and git operations."""

import re
import subprocess
from datetime import datetime
from typing import Optional

from github import Auth, Github
from github.Issue import Issue
from github.Repository import Repository

from .config import AutoClaudeConfig
from .models import BranchInfo, CIStatus, IssueComment, IssueContext
from .platform import WorkItem


class GitHubClient:
    """Client for GitHub API operations."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config
        self._github = Github(auth=Auth.Token(config.github_token))
        self._repo: Optional[Repository] = None

    @property
    def repo(self) -> Repository:
        if self._repo is None:
            self._repo = self._github.get_repo(self.config.repo)
        return self._repo

    def get_assigned_issues(self) -> list[WorkItem]:
        """Get all open issues assigned to the bot."""
        issues = self.repo.get_issues(
            state="open",
            assignee=self.config.bot_assignee,
        )
        return [WorkItem(number=i.number, title=i.title, raw=i) for i in issues]

    def get_claimable_issues(self, require_label: str = "enhancement") -> list[WorkItem]:
        """Get open issues that can be claimed by an agent.

        Returns issues that have the required label but NOT agent-claimed/blocked/complete.
        """
        issues = self.repo.get_issues(state="open", labels=[require_label])

        agent_labels = {
            self.config.label_in_progress,
            self.config.label_blocked,
            self.config.label_completed,
        }

        claimable = []
        for issue in issues:
            issue_labels = {label.name for label in issue.labels}
            if not issue_labels.intersection(agent_labels):
                claimable.append(WorkItem(number=issue.number, title=issue.title, raw=issue))

        return claimable

    def get_issues_with_label(self, label: str) -> list[WorkItem]:
        """Get all open issues with a specific label."""
        issues = self.repo.get_issues(state="open", labels=[label])
        return [WorkItem(number=i.number, title=i.title, raw=i) for i in issues]

    def is_claimed(self, issue_number: int) -> bool:
        """Check if an issue is already claimed by an agent."""
        item = self.get_issue(issue_number)
        issue = item.raw
        issue_labels = {label.name for label in issue.labels}
        return self.config.label_in_progress in issue_labels

    def get_issue(self, issue_number: int) -> WorkItem:
        """Get a specific issue by number."""
        issue = self.repo.get_issue(issue_number)
        return WorkItem(number=issue.number, title=issue.title, raw=issue)

    def build_issue_context(self, item: WorkItem) -> IssueContext:
        """Build full context from an issue for processing."""
        issue = item.raw
        comments = []
        for comment in issue.get_comments():
            comments.append(IssueComment(
                id=comment.id,
                author=comment.user.login if comment.user else "unknown",
                body=comment.body or "",
                created_at=comment.created_at,
                is_bot=comment.user.type == "Bot" if comment.user else False,
            ))

        context = IssueContext(
            number=issue.number,
            title=issue.title,
            body=issue.body or "",
            author=issue.user.login if issue.user else "unknown",
            labels=[label.name for label in issue.labels],
            assignees=[a.login for a in issue.assignees],
            comments=comments,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            url=issue.html_url,
        )

        context.referenced_files = self._extract_file_references(context)
        context.error_messages = self._extract_error_messages(context)
        context.linked_prs = self._extract_linked_prs(context)
        context.linked_issues = self._extract_linked_issues(context)

        return context

    def _extract_file_references(self, context: IssueContext) -> list[str]:
        """Extract file paths mentioned in issue."""
        all_text = context.body + "\n" + "\n".join(c.body for c in context.comments)

        patterns = [
            r'`([a-zA-Z0-9_\-./]+\.[a-zA-Z]+)`',
            r'(?:^|\s)([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-./]+\.[a-zA-Z]+)',
            r'(?:in|at|file|see)\s+[`"]?([a-zA-Z0-9_\-./]+\.[a-zA-Z]+)[`"]?',
        ]

        files = set()
        for pattern in patterns:
            matches = re.findall(pattern, all_text, re.MULTILINE)
            files.update(matches)

        return sorted(files)

    def _extract_error_messages(self, context: IssueContext) -> list[str]:
        """Extract error messages/stack traces from issue."""
        all_text = context.body + "\n" + "\n".join(c.body for c in context.comments)

        code_blocks = re.findall(r'```(?:[\w]*\n)?(.*?)```', all_text, re.DOTALL)
        errors = []

        error_indicators = ['error', 'exception', 'traceback', 'failed', 'fatal']
        for block in code_blocks:
            if any(indicator in block.lower() for indicator in error_indicators):
                errors.append(block.strip())

        return errors

    def _extract_linked_prs(self, context: IssueContext) -> list[int]:
        """Extract linked PR numbers from issue."""
        all_text = context.body + "\n" + "\n".join(c.body for c in context.comments)
        matches = re.findall(r'(?:PR\s*#?|pull/|#)(\d+)', all_text, re.IGNORECASE)
        return sorted(set(int(m) for m in matches if int(m) != context.number))

    def _extract_linked_issues(self, context: IssueContext) -> list[int]:
        """Extract linked issue numbers."""
        all_text = context.body + "\n" + "\n".join(c.body for c in context.comments)
        matches = re.findall(r'(?:issue\s*#?|fixes\s*#?|closes\s*#?|relates?\s*to\s*#?)(\d+)', all_text, re.IGNORECASE)
        return sorted(set(int(m) for m in matches if int(m) != context.number))

    def _get_raw_issue(self, issue_number: int) -> Issue:
        """Get raw GitHub Issue object for API operations."""
        return self.repo.get_issue(issue_number)

    def add_comment(self, issue_number: int, body: str) -> None:
        if self.config.dry_run:
            print(f"[DRY RUN] Would add comment to #{issue_number}:\n{body[:200]}...", flush=True)
            return
        issue = self._get_raw_issue(issue_number)
        issue.create_comment(body)

    def add_label(self, issue_number: int, label: str) -> None:
        if self.config.dry_run:
            print(f"[DRY RUN] Would add label '{label}' to #{issue_number}", flush=True)
            return
        issue = self._get_raw_issue(issue_number)
        issue.add_to_labels(label)

    def remove_label(self, issue_number: int, label: str) -> None:
        if self.config.dry_run:
            print(f"[DRY RUN] Would remove label '{label}' from #{issue_number}", flush=True)
            return
        issue = self._get_raw_issue(issue_number)
        try:
            issue.remove_from_labels(label)
        except Exception:
            pass

    def set_assignees(self, issue_number: int, assignees: list[str]) -> None:
        if self.config.dry_run:
            print(f"[DRY RUN] Would set assignees on #{issue_number}: {assignees}", flush=True)
            return
        issue = self._get_raw_issue(issue_number)
        for assignee in issue.assignees:
            issue.remove_from_assignees(assignee)
        for assignee in assignees:
            issue.add_to_assignees(assignee)

    def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = False,
    ) -> tuple[int, str]:
        """Create a pull request, or return existing one for the branch.

        Returns (pr_number, pr_url).
        """
        if self.config.dry_run:
            print(f"[DRY RUN] Would create PR: {title}", flush=True)
            return (0, "https://github.com/dry-run/pr")

        # Check for existing PR on this branch before creating a duplicate.
        existing = list(self.repo.get_pulls(state="open", head=f"{self.repo.owner.login}:{head}"))
        if existing:
            pr = existing[0]
            print(f"  PR #{pr.number} already exists for branch {head}, skipping creation", flush=True)
            return (pr.number, pr.html_url)

        pr = self.repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
            draft=draft,
        )
        return (pr.number, pr.html_url)

    def get_ci_status(self, branch: str) -> CIStatus:
        """Get CI status for a branch using Actions workflow runs API.

        Uses /actions/runs (requires Actions:Read) instead of /check-runs
        (requires Checks:Read) since fine-grained PATs may not expose the
        Checks permission.
        """
        try:
            branch_ref = self.repo.get_branch(branch)
            sha = branch_ref.commit.sha
        except Exception:
            return CIStatus(conclusion=None, status="unknown")

        workflow_runs = list(self.repo.get_workflow_runs(head_sha=sha))

        if not workflow_runs:
            return CIStatus(conclusion="success", status="completed", check_runs=[])

        conclusions = [run.conclusion for run in workflow_runs if run.conclusion]
        statuses = [run.status for run in workflow_runs]

        if "in_progress" in statuses or "queued" in statuses:
            status = "in_progress"
            conclusion = None
        elif all(c == "success" for c in conclusions):
            status = "completed"
            conclusion = "success"
        elif any(c == "failure" for c in conclusions):
            status = "completed"
            conclusion = "failure"
        else:
            status = "completed"
            conclusion = conclusions[0] if conclusions else None

        return CIStatus(
            conclusion=conclusion,
            status=status,
            workflow_runs=[{
                "name": run.name,
                "conclusion": run.conclusion,
                "status": run.status,
            } for run in workflow_runs],
        )


class GitOperations:
    """Git operations for branch and commit management."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config
        self.worktree_path: str | None = None

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command, optionally in the worktree directory."""
        cmd = ["git"] + list(args)
        cwd = self.worktree_path or self.config.repo_dir or None
        if self.config.dry_run:
            cwd_msg = f" (in {cwd})" if cwd else ""
            print(f"[DRY RUN] Would run: {' '.join(cmd)}{cwd_msg}", flush=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)

    def create_worktree(self, issue_number: int, title: str) -> BranchInfo:
        """Create a new git worktree for an issue (isolated from main repo)."""
        import os

        slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower())[:30].strip('-')
        branch_name = f"issue-{issue_number}-{slug}"

        base_path = self.config.worktree_base_path
        repo_name = self.config.repo_name or "repo"
        worktree_dir = f"{repo_name}-issue-{issue_number}"
        worktree_path = os.path.abspath(os.path.join(base_path, worktree_dir))

        remote = self.config.git_remote
        self._run_git("fetch", remote)

        result = self._run_git(
            "worktree", "add", worktree_path, "-b", branch_name, f"{remote}/{self.config.base_branch}",
            check=False
        )

        if result.returncode != 0:
            if "already exists" in result.stderr:
                result = self._run_git(
                    "worktree", "add", worktree_path, branch_name,
                    check=False
                )
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to create worktree: {result.stderr}")
            else:
                raise RuntimeError(f"Failed to create worktree: {result.stderr}")

        self.worktree_path = worktree_path
        self.config.worktree_path = worktree_path

        print(f"Created worktree at: {worktree_path}", flush=True)

        return BranchInfo(
            name=branch_name,
            issue_number=issue_number,
            created=True,
            worktree_path=worktree_path,
        )

    def create_branch(self, issue_number: int, title: str) -> BranchInfo:
        """Create a new branch for an issue."""
        if self.config.use_worktree:
            return self.create_worktree(issue_number, title)

        slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower())[:30].strip('-')
        branch_name = f"issue-{issue_number}-{slug}"

        remote = self.config.git_remote
        self._run_git("fetch", remote)
        self._run_git("checkout", "-b", branch_name, f"{remote}/{self.config.base_branch}")

        return BranchInfo(
            name=branch_name,
            issue_number=issue_number,
            created=True,
        )

    def checkout_branch(self, branch_name: str) -> None:
        self._run_git("checkout", branch_name)

    def current_branch(self) -> str:
        result = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def push_branch(self, branch_name: str) -> None:
        self._run_git("push", "-u", self.config.git_remote, branch_name)

    def commit(self, message: str, issue_number: int) -> str:
        """Create a commit with issue reference. Returns commit SHA."""
        if f"#{issue_number}" not in message:
            message = f"{message} (#{issue_number})"

        # Stage everything EXCEPT .autoclaude/ (progress tracking, not code)
        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", ".autoclaude/", check=False)
        result = self._run_git("commit", "-m", message, check=False)

        if result.returncode != 0:
            combined = result.stdout + result.stderr
            if "nothing to commit" in combined:
                return ""
            # Include both stdout and stderr for diagnosis
            error_detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Commit failed: {error_detail}")

        sha_result = self._run_git("rev-parse", "HEAD")
        return sha_result.stdout.strip()

    def has_uncommitted_changes(self) -> bool:
        result = self._run_git("status", "--porcelain")
        return bool(result.stdout.strip())

    def has_code_changes(self, base: str | None = None) -> bool:
        """Check if there are meaningful code changes (not just .autoclaude/)."""
        if base is None:
            base = f"{self.config.git_remote}/{self.config.base_branch}"
        # Check uncommitted changes first
        porcelain = self._run_git("status", "--porcelain")
        for line in porcelain.stdout.strip().splitlines():
            filepath = line[3:].strip().strip('"')
            if not filepath.startswith(".autoclaude/"):
                return True
        # Check committed but unpushed changes
        diff_result = self._run_git("diff", "--name-only", f"{base}..HEAD", check=False)
        if diff_result.returncode == 0:
            for filepath in diff_result.stdout.strip().splitlines():
                if not filepath.startswith(".autoclaude/"):
                    return True
        return False

    def get_commit_count(self, base: str | None = None) -> int:
        if base is None:
            base = f"{self.config.git_remote}/{self.config.base_branch}"
        result = self._run_git("rev-list", "--count", f"{base}..HEAD")
        return int(result.stdout.strip())

    def get_diff_summary(self, base: str | None = None) -> str:
        """Get a summary of changes since base for iteration context."""
        if base is None:
            base = f"{self.config.git_remote}/{self.config.base_branch}"
        result = self._run_git("diff", "--stat", f"{base}..HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def cleanup_worktree(self, branch_name: str) -> None:
        """Remove worktree and delete branch after processing."""
        if not self.worktree_path:
            return
        # Must run from the main repo, not the worktree
        old_worktree = self.worktree_path
        self.worktree_path = None
        try:
            self._run_git("worktree", "remove", old_worktree, "--force", check=False)
            self._run_git("branch", "-D", branch_name, check=False)
        except Exception:
            pass  # Best-effort cleanup
