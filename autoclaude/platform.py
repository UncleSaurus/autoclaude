"""Platform abstraction for ticket processing.

Defines a TicketPlatform protocol that both GitHubClient and AdoClient implement,
allowing the processor to work with either platform without code changes.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .models import CIStatus, IssueContext


@dataclass
class WorkItem:
    """Platform-agnostic work item wrapper."""

    number: int
    title: str
    raw: Any  # Platform-specific object (GitHub Issue, ADO work item dict, etc.)


@runtime_checkable
class TicketPlatform(Protocol):
    """Protocol for ticket platform operations.

    Both GitHubClient and AdoClient implement this protocol,
    allowing TicketProcessor to work with either platform.
    """

    def get_assigned_issues(self) -> list[WorkItem]: ...

    def get_claimable_issues(self, require_label: str = "enhancement") -> list[WorkItem]: ...

    def is_claimed(self, issue_number: int) -> bool: ...

    def get_issue(self, issue_number: int) -> WorkItem: ...

    def build_issue_context(self, item: WorkItem) -> IssueContext: ...

    def add_comment(self, issue_number: int, body: str) -> None: ...

    def add_label(self, issue_number: int, label: str) -> None: ...

    def remove_label(self, issue_number: int, label: str) -> None: ...

    def set_assignees(self, issue_number: int, assignees: list[str]) -> None: ...

    def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = False,
    ) -> tuple[int, str]: ...

    def get_ci_status(self, branch: str) -> CIStatus: ...
