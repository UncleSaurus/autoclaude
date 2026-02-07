"""Tests for config and models (no external dependencies needed)."""

from datetime import datetime

from autoclaude.config import AutoClaudeConfig
from autoclaude.models import (
    BranchInfo,
    CIStatus,
    ClarificationOption,
    ClarificationQuestion,
    ClarificationRequest,
    IssueComment,
    IssueContext,
    ProcessingResult,
    ProcessingStatus,
)


def test_config_defaults():
    config = AutoClaudeConfig(repo="owner/repo")
    assert config.repo == "owner/repo"
    assert config.max_turns == 50
    assert config.max_iterations == 1
    assert config.no_context is False
    assert config.context_dir is None


def test_config_validate_missing_token():
    config = AutoClaudeConfig(repo="owner/repo", github_token="", anthropic_api_key="")
    errors = config.validate()
    assert any("GITHUB_TOKEN" in e for e in errors)
    # anthropic_api_key is optional (falls back to Claude CLI OAuth)


def test_config_validate_bad_repo():
    config = AutoClaudeConfig(repo="noslash", github_token="x", anthropic_api_key="x")
    errors = config.validate()
    assert any("owner/repo" in e for e in errors)


def test_config_repo_properties():
    config = AutoClaudeConfig(repo="owner/myrepo")
    assert config.repo_owner == "owner"
    assert config.repo_name == "myrepo"


def test_issue_context_format():
    ctx = IssueContext(
        number=42,
        title="Fix the bug",
        body="Something is broken",
        author="user",
        labels=["bug"],
        assignees=["bot"],
        comments=[],
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 2),
        url="https://github.com/owner/repo/issues/42",
    )
    prompt = ctx.format_for_prompt()
    assert "Issue #42" in prompt
    assert "Fix the bug" in prompt
    assert "Something is broken" in prompt
    assert "bug" in prompt


def test_issue_context_with_comments():
    ctx = IssueContext(
        number=1,
        title="Test",
        body="Body",
        author="a",
        labels=[],
        assignees=[],
        comments=[
            IssueComment(id=1, author="bob", body="Looks good", created_at=datetime(2026, 1, 1)),
        ],
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        url="",
    )
    prompt = ctx.format_for_prompt()
    assert "bob" in prompt
    assert "Looks good" in prompt


def test_processing_result_summary():
    result = ProcessingResult(
        issue_number=42,
        status=ProcessingStatus.COMPLETED,
        branch_name="issue-42-fix",
        pr_url="https://github.com/owner/repo/pull/43",
        commits=["abc123"],
    )
    summary = result.summary()
    assert "#42" in summary
    assert "completed" in summary
    assert "issue-42-fix" in summary
    assert "pull/43" in summary


def test_ci_status_properties():
    status = CIStatus(conclusion="success", status="completed")
    assert status.is_success
    assert not status.is_failure
    assert not status.is_pending

    status = CIStatus(conclusion="failure", status="completed")
    assert not status.is_success
    assert status.is_failure

    status = CIStatus(conclusion=None, status="in_progress")
    assert status.is_pending


def test_clarification_question_markdown():
    q = ClarificationQuestion(
        id="SCOPE",
        question="Which endpoints?",
        options=[
            ClarificationOption(label="All endpoints"),
            ClarificationOption(label="Auth only"),
        ],
    )
    md = q.to_markdown()
    assert "[SCOPE]" in md
    assert "Which endpoints?" in md
    assert "All endpoints" in md
    assert "Other:" in md


def test_clarification_request_markdown():
    req = ClarificationRequest(
        questions=[
            ClarificationQuestion(id="Q1", question="First?", options=[
                ClarificationOption(label="A"),
                ClarificationOption(label="B"),
            ]),
        ],
    )
    md = req.to_markdown()
    assert "Clarification Needed" in md
    assert "First?" in md


def test_branch_info():
    info = BranchInfo(name="issue-42-fix", issue_number=42, created=True)
    assert info.name == "issue-42-fix"
    assert info.worktree_path is None
