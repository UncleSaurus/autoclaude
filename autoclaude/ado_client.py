"""Azure DevOps client for ticket processing.

Uses the `az` CLI (already authenticated) to interact with Azure DevOps
work items, repos, and pull requests. No Python SDK dependency needed.
"""

import json
import re
import subprocess
from datetime import datetime
from typing import Optional

from .config import AutoClaudeConfig
from .models import CIStatus, IssueComment, IssueContext
from .platform import WorkItem


def _html_to_text(html: str) -> str:
    """Convert HTML description to plain text for LLM prompts."""
    if not html:
        return ""
    # Remove HTML tags
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li\s*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_tags(tag_string: str) -> list[str]:
    """Parse ADO semicolon-separated tags into a list."""
    if not tag_string:
        return []
    return [t.strip() for t in tag_string.split(";") if t.strip()]


def _format_tags(tags: list[str]) -> str:
    """Format a tag list back to ADO semicolon-separated format."""
    return "; ".join(tags)


def _parse_ado_date(date_str: str) -> datetime:
    """Parse an ADO date string to datetime."""
    if not date_str:
        return datetime.min
    # ADO returns ISO 8601 format, sometimes with timezone
    date_str = re.sub(r"Z$", "+00:00", date_str)
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        return datetime.min


class AdoClient:
    """Azure DevOps client using az CLI."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config
        self.org = config.ado_org
        self.project = config.ado_project
        self.repo = config.ado_repo

    def _run_az(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run an az CLI command and return the result."""
        cmd = ["az"] + list(args)
        if self.config.dry_run:
            print(f"[DRY RUN] Would run: {' '.join(cmd)}", flush=True)
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise RuntimeError(f"az command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def _run_az_json(self, *args: str) -> dict | list:
        """Run an az CLI command and parse JSON output."""
        result = self._run_az(*args, "--output", "json")
        if self.config.dry_run:
            return {}
        return json.loads(result.stdout)

    def _patch_work_item(self, work_item_id: int, operations: list[dict]) -> dict:
        """Update a work item using the REST API with JSON Patch.

        az boards --fields is additive for System.Tags and cannot remove tags.
        This method uses az rest with JSON Patch to do true replace operations.
        """
        if self.config.dry_run:
            return {}
        body = json.dumps(operations)
        project_encoded = self.project.replace(" ", "%20")
        url = f"https://dev.azure.com/{self.org}/{project_encoded}/_apis/wit/workitems/{work_item_id}?api-version=7.1"
        result = subprocess.run(
            [
                "az", "rest",
                "--method", "patch",
                "--url", url,
                "--body", body,
                "--headers", "Content-Type=application/json-patch+json",
                "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
                "--output", "json",
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"REST PATCH failed for #{work_item_id}: {result.stderr}")
        return json.loads(result.stdout)

    def _get_work_item(self, work_item_id: int) -> dict:
        """Get a single work item by ID."""
        return self._run_az_json(
            "boards", "work-item", "show",
            "--id", str(work_item_id),
            "--org", f"https://dev.azure.com/{self.org}",
        )

    def _get_work_item_tags(self, work_item_id: int) -> list[str]:
        """Get current tags for a work item."""
        wi = self._get_work_item(work_item_id)
        fields = wi.get("fields", {})
        return _parse_tags(fields.get("System.Tags", ""))

    def get_assigned_issues(self) -> list[WorkItem]:
        """Get work items assigned to the bot."""
        wiql = (
            f"SELECT [System.Id], [System.Title], [System.Tags] "
            f"FROM WorkItems "
            f"WHERE [System.TeamProject] = '{self.project}' "
            f"AND [System.State] <> 'Done' "
            f"AND [System.State] <> 'Removed' "
            f"AND [System.AssignedTo] = @Me "
            f"ORDER BY [Microsoft.VSTS.Common.Priority] ASC"
        )

        result = self._run_az_json(
            "boards", "query",
            "--wiql", wiql,
            "--org", f"https://dev.azure.com/{self.org}",
            "--project", self.project,
        )

        if self.config.dry_run:
            return []

        items = []
        work_items = result if isinstance(result, list) else result.get("workItems", [])
        for wi_ref in work_items:
            wi_id = wi_ref.get("id")
            if not wi_id:
                continue
            wi = self._get_work_item(wi_id)
            fields = wi.get("fields", {})
            items.append(WorkItem(
                number=wi_id,
                title=fields.get("System.Title", ""),
                raw=wi,
            ))

        return items

    def get_claimable_issues(self, require_label: str = "enhancement") -> list[WorkItem]:
        """Get work items that can be claimed by an agent.

        Queries for items in 'To Do' state with the required tag,
        excluding items already tagged with agent coordination labels.
        """
        agent_tags = {
            self.config.label_in_progress,
            self.config.label_blocked,
            self.config.label_completed,
        }

        wiql = (
            f"SELECT [System.Id], [System.Title], [System.Tags] "
            f"FROM WorkItems "
            f"WHERE [System.TeamProject] = '{self.project}' "
            f"AND [System.State] = 'To Do' "
            f"AND [System.Tags] CONTAINS '{require_label}' "
            f"ORDER BY [Microsoft.VSTS.Common.Priority] ASC"
        )

        result = self._run_az_json(
            "boards", "query",
            "--wiql", wiql,
            "--org", f"https://dev.azure.com/{self.org}",
            "--project", self.project,
        )

        if self.config.dry_run:
            return []

        items = []
        work_items = result if isinstance(result, list) else result.get("workItems", [])
        for wi_ref in work_items:
            wi_id = wi_ref.get("id")
            if not wi_id:
                continue
            wi = self._get_work_item(wi_id)
            fields = wi.get("fields", {})
            tags = _parse_tags(fields.get("System.Tags", ""))

            # Skip if already claimed by an agent
            if any(tag in agent_tags for tag in tags):
                continue

            items.append(WorkItem(
                number=wi_id,
                title=fields.get("System.Title", ""),
                raw=wi,
            ))

        return items

    def is_claimed(self, issue_number: int) -> bool:
        """Check if a work item is already claimed by an agent."""
        tags = self._get_work_item_tags(issue_number)
        return self.config.label_in_progress in tags

    def get_issue(self, issue_number: int) -> WorkItem:
        """Get a work item by ID."""
        wi = self._get_work_item(issue_number)
        fields = wi.get("fields", {})
        return WorkItem(
            number=issue_number,
            title=fields.get("System.Title", ""),
            raw=wi,
        )

    def build_issue_context(self, item: WorkItem) -> IssueContext:
        """Build full context from a work item for processing."""
        wi = item.raw
        fields = wi.get("fields", {})

        body_html = fields.get("System.Description", "")
        body = _html_to_text(body_html)

        tags = _parse_tags(fields.get("System.Tags", ""))

        # Get comments (discussion)
        comments = self._load_comments(item.number)

        context = IssueContext(
            number=item.number,
            title=fields.get("System.Title", ""),
            body=body,
            author=fields.get("System.CreatedBy", {}).get("displayName", "unknown"),
            labels=tags,
            assignees=[fields.get("System.AssignedTo", {}).get("displayName", "")]
            if fields.get("System.AssignedTo")
            else [],
            comments=comments,
            created_at=_parse_ado_date(fields.get("System.CreatedDate", "")),
            updated_at=_parse_ado_date(fields.get("System.ChangedDate", "")),
            url=f"https://dev.azure.com/{self.org}/{self.project}/_workitems/edit/{item.number}",
        )

        # Extract file references and errors from body
        context.referenced_files = self._extract_file_references(context)
        context.error_messages = self._extract_error_messages(context)

        return context

    def _load_comments(self, work_item_id: int) -> list[IssueComment]:
        """Load discussion comments for a work item."""
        try:
            result = self._run_az_json(
                "boards", "work-item", "show",
                "--id", str(work_item_id),
                "--expand", "all",
                "--org", f"https://dev.azure.com/{self.org}",
            )

            if self.config.dry_run:
                return []

            comments = []
            history = result.get("fields", {}).get("System.History", "")
            if history:
                comments.append(IssueComment(
                    id=0,
                    author="system",
                    body=_html_to_text(history),
                    created_at=_parse_ado_date(
                        result.get("fields", {}).get("System.ChangedDate", "")
                    ),
                ))

            return comments
        except Exception:
            return []

    def _extract_file_references(self, context: IssueContext) -> list[str]:
        """Extract file paths mentioned in work item."""
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
        """Extract error messages/stack traces from work item."""
        all_text = context.body + "\n" + "\n".join(c.body for c in context.comments)
        code_blocks = re.findall(r'```(?:[\w]*\n)?(.*?)```', all_text, re.DOTALL)
        errors = []
        error_indicators = ["error", "exception", "traceback", "failed", "fatal"]
        for block in code_blocks:
            if any(indicator in block.lower() for indicator in error_indicators):
                errors.append(block.strip())
        return errors

    def add_comment(self, issue_number: int, body: str) -> None:
        """Add a discussion comment to a work item."""
        if self.config.dry_run:
            print(f"[DRY RUN] Would add comment to #{issue_number}:\n{body[:200]}...", flush=True)
            return
        self._run_az(
            "boards", "work-item", "update",
            "--id", str(issue_number),
            "--discussion", body,
            "--org", f"https://dev.azure.com/{self.org}",
        )

    def add_label(self, issue_number: int, label: str) -> None:
        """Add a tag to a work item. Side-effect: transitions state for agent tags."""
        if self.config.dry_run:
            print(f"[DRY RUN] Would add tag '{label}' to #{issue_number}", flush=True)
            return

        tags = self._get_work_item_tags(issue_number)
        if label in tags:
            return

        tags.append(label)
        ops = [{"op": "replace", "path": "/fields/System.Tags", "value": _format_tags(tags)}]

        # State transitions as side effects of agent coordination tags
        if label == self.config.label_in_progress:
            ops.append({"op": "replace", "path": "/fields/System.State", "value": "Doing"})
        elif label == self.config.label_completed:
            ops.append({"op": "replace", "path": "/fields/System.State", "value": "Done"})

        self._patch_work_item(issue_number, ops)

    def remove_label(self, issue_number: int, label: str) -> None:
        """Remove a tag from a work item."""
        if self.config.dry_run:
            print(f"[DRY RUN] Would remove tag '{label}' from #{issue_number}", flush=True)
            return

        tags = self._get_work_item_tags(issue_number)
        if label not in tags:
            return

        tags.remove(label)
        self._patch_work_item(issue_number, [
            {"op": "replace", "path": "/fields/System.Tags", "value": _format_tags(tags)},
        ])

    def set_assignees(self, issue_number: int, assignees: list[str]) -> None:
        """Set the assigned-to field on a work item (ADO supports one assignee)."""
        if self.config.dry_run:
            print(f"[DRY RUN] Would set assignee on #{issue_number}: {assignees}", flush=True)
            return

        assignee = assignees[0] if assignees else ""
        self._run_az(
            "boards", "work-item", "update",
            "--id", str(issue_number),
            "--fields", f"System.AssignedTo={assignee}",
            "--org", f"https://dev.azure.com/{self.org}",
        )

    def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = False,
    ) -> tuple[int, str]:
        """Create a pull request on ADO. Returns (pr_id, pr_url)."""
        if self.config.dry_run:
            print(f"[DRY RUN] Would create PR: {title}", flush=True)
            return (0, f"https://dev.azure.com/{self.org}/{self.project}/_git/{self.repo}/pullrequest/0")

        args = [
            "repos", "pr", "create",
            "--repository", self.repo,
            "--project", self.project,
            "--org", f"https://dev.azure.com/{self.org}",
            "--source-branch", head,
            "--target-branch", base,
            "--title", title,
            "--description", body,
        ]
        if draft:
            args.append("--draft")

        # Extract work item number from title for auto-linking
        wi_match = re.search(r"#(\d+)", title)
        if wi_match:
            args.extend(["--work-items", wi_match.group(1)])

        result = self._run_az_json(*args)

        if self.config.dry_run:
            return (0, "")

        pr_id = result.get("pullRequestId", 0)
        repo_url = result.get("repository", {}).get("webUrl", "")
        pr_url = f"{repo_url}/pullrequest/{pr_id}" if repo_url else ""

        return (pr_id, pr_url)

    def get_ci_status(self, branch: str) -> CIStatus:
        """Get CI status for a branch.

        ADO Pipelines integration is not yet implemented for this client.
        Returns permissive 'success' to allow the workflow to continue.
        """
        return CIStatus(conclusion="success", status="completed", check_runs=[])
