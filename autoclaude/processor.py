"""Core ticket processing workflow."""

import asyncio
import time
import uuid
from datetime import datetime
from typing import Optional

from .agent import AgentRunner
from .config import AutoClaudeConfig
from .context import load_context, load_progress_context
from .github_client import GitHubClient, GitOperations
from .loop import IterationLoop
from .models import ClarificationRequest, IssueContext, ProcessingResult, ProcessingStatus
from .progress import append_run, extract_learnings


class TicketProcessor:
    """Orchestrates the full ticket processing workflow."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config
        self.github = GitHubClient(config)
        self.git = GitOperations(config)
        self.agent = AgentRunner(config)
        self.loop = IterationLoop(config)
        self.session_id = str(uuid.uuid4())[:8]

    def _get_context_root(self) -> str:
        return self.config.context_dir or self.config.worktree_path or "."

    def _load_project_context(self) -> str:
        """Load project context for agent prompts."""
        if self.config.no_context:
            return ""

        context_root = self._get_context_root()
        project_context = load_context(context_root)
        progress_context = load_progress_context(context_root)

        if progress_context:
            return project_context + "\n" + progress_context if project_context else progress_context
        return project_context

    async def process_claimable(self, require_label: str = "enhancement") -> list[ProcessingResult]:
        """Process all claimable issues (label-based coordination)."""
        issues = self.github.get_claimable_issues(require_label)
        results = []

        print(f"Found {len(issues)} claimable issues with label '{require_label}'")

        for issue in issues:
            if self.github.is_claimed(issue.number):
                print(f"  Issue #{issue.number} was claimed by another agent, skipping")
                continue

            context = self.github.build_issue_context(issue)
            print(f"\nClaiming and processing issue #{context.number}: {context.title}")
            result = await self.process_issue(context)
            results.append(result)
            print(f"Result: {result.status.value}")

        return results

    async def process_all_assigned(self) -> list[ProcessingResult]:
        """Process all issues assigned to the bot."""
        issues = self.github.get_assigned_issues()
        results = []

        print(f"Found {len(issues)} issues assigned to {self.config.bot_assignee}")

        for issue in issues:
            context = self.github.build_issue_context(issue)
            print(f"\nProcessing issue #{context.number}: {context.title}")
            result = await self.process_issue(context)
            results.append(result)
            print(f"Result: {result.status.value}")

        return results

    async def process_single(self, issue_number: int) -> ProcessingResult:
        """Process a single issue by number."""
        issue = self.github.get_issue(issue_number)
        context = self.github.build_issue_context(issue)
        return await self.process_issue(context)

    async def process_issue(self, context: IssueContext) -> ProcessingResult:
        """Process a single issue through the full workflow."""
        result = ProcessingResult(
            issue_number=context.number,
            status=ProcessingStatus.IN_PROGRESS,
            started_at=datetime.now(),
        )

        try:
            # Check if issue has 'agent-ready' label (skip clarification)
            skip_clarification = (
                self.config.skip_clarification or
                "agent-ready" in context.labels
            )

            # Step 0: Clarification phase (unless skipped)
            if not skip_clarification:
                print(f"  Analyzing issue for clarity...")
                self._mark_analyzing(context.number)

                if self.config.dry_run:
                    print(f"  [DRY RUN] Would analyze issue #{context.number}")
                else:
                    analysis = await self.agent.analyze_issue(context)

                    if analysis.error:
                        return self._handle_error(result, context, f"Analysis failed: {analysis.error}")

                    if not analysis.ready_to_implement and analysis.clarification_request:
                        return self._request_clarification(result, context, analysis.clarification_request)

                    if analysis.implementation_plan:
                        print(f"  Ready to implement: {analysis.implementation_plan[:100]}...")

            # Step 1: Add in-progress label
            self._mark_in_progress(context.number)

            # Step 2: Create branch
            print(f"  Creating branch for issue #{context.number}...")
            branch = self.git.create_branch(context.number, context.title)
            result.branch_name = branch.name
            print(f"  Branch: {branch.name}")

            # Step 3: Optionally post plan comment
            if self.config.post_plan_comment:
                self._post_starting_comment(context)

            # Step 4: Run agent (skip in dry-run mode)
            if self.config.dry_run:
                print(f"  [DRY RUN] Would run agent for issue #{context.number}")
                print(f"  [DRY RUN] Issue: {context.title}")
                print(f"  [DRY RUN] Context loaded: {bool(self._load_project_context())}")
                result.status = ProcessingStatus.COMPLETED
                result.completed_at = datetime.now()
                return result

            # Use iteration loop if max_iterations > 1, otherwise single pass
            if self.config.max_iterations > 1:
                print(f"  Running with up to {self.config.max_iterations} iterations...")
                agent_result = await self.loop.run_issue_loop(context)
            else:
                print(f"  Running agent...")
                project_context = self._load_project_context()
                agent_result = await self.agent.run(context, project_context=project_context)

                # Record single-pass progress
                learnings = extract_learnings(agent_result.output)
                context_root = self._get_context_root()
                status = "COMPLETED" if agent_result.success else ("BLOCKED" if agent_result.blocked else "FAILED")
                append_run(
                    root=context_root,
                    issue_number=context.number,
                    title=context.title,
                    status=status,
                    branch=branch.name,
                    learnings=learnings,
                )

            if agent_result.blocked:
                return self._handle_blocked(result, context, agent_result.blocking_question)

            if agent_result.error:
                return self._handle_error(result, context, agent_result.error)

            # Step 5: Commit any uncommitted changes
            if self.git.has_uncommitted_changes():
                sha = self.git.commit("Complete implementation", context.number)
                if sha:
                    result.commits.append(sha)

            # Step 6: Push and wait for CI
            print(f"  Pushing branch and waiting for CI...")
            self.git.push_branch(branch.name)

            ci_result = await self._wait_for_ci(branch.name, context, result)
            if not ci_result:
                return result

            # Step 7: Create PR
            print(f"  Creating pull request...")
            pr_number, pr_url = self._create_pr(context, branch.name)
            result.pr_number = pr_number
            result.pr_url = pr_url

            # Update progress with PR info
            context_root = self._get_context_root()
            append_run(
                root=context_root,
                issue_number=context.number,
                title=context.title,
                status="PR_CREATED",
                branch=branch.name,
                pr_url=pr_url,
            )

            # Step 8: Update issue
            self._mark_completed(context.number, pr_url)
            result.status = ProcessingStatus.COMPLETED
            result.completed_at = datetime.now()

            return result

        except Exception as e:
            return self._handle_error(result, context, str(e))

    def _mark_analyzing(self, issue_number: int) -> None:
        self.github.add_label(issue_number, self.config.label_analyzing)
        self.github.remove_label(issue_number, self.config.label_blocked)
        self.github.remove_label(issue_number, self.config.label_failed)

    def _mark_in_progress(self, issue_number: int) -> None:
        self.github.remove_label(issue_number, self.config.label_analyzing)
        self.github.remove_label(issue_number, self.config.label_clarifying)
        self.github.add_label(issue_number, self.config.label_in_progress)
        self.github.remove_label(issue_number, self.config.label_blocked)
        self.github.remove_label(issue_number, self.config.label_failed)

    def _post_starting_comment(self, context: IssueContext) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        comment = f"""**AutoClaude Claimed** | Session: `{self.session_id}` | Started: {timestamp}

**Branch:** `issue-{context.number}-...`
**Model:** {self.config.model}
**Iterations:** {self.config.max_iterations}

I'll update this issue when processing is complete or if I have questions."""

        self.github.add_comment(context.number, comment)

    def _request_clarification(
        self,
        result: ProcessingResult,
        context: IssueContext,
        clarification: ClarificationRequest,
    ) -> ProcessingResult:
        result.status = ProcessingStatus.CLARIFYING
        result.completed_at = datetime.now()

        comment = f"""**AutoClaude Analyzing** | Session: `{self.session_id}`

{clarification.to_markdown()}

---
*Once you've answered above, react with :+1: on this comment or reply "ready" to proceed.*
"""

        self.github.add_comment(context.number, comment)
        self.github.remove_label(context.number, self.config.label_analyzing)
        self.github.add_label(context.number, self.config.label_clarifying)

        print(f"  CLARIFYING: Posted {len(clarification.questions)} questions")
        return result

    def _handle_blocked(
        self,
        result: ProcessingResult,
        context: IssueContext,
        question: Optional[str],
    ) -> ProcessingResult:
        result.status = ProcessingStatus.BLOCKED
        result.blocking_question = question
        result.completed_at = datetime.now()

        comment = f"""**AutoClaude Blocked** | Session: `{self.session_id}`

I need clarification to continue:

> {question}

Please respond to this question, then remove the `{self.config.label_blocked}` label to allow a retry."""

        self.github.add_comment(context.number, comment)
        self.github.remove_label(context.number, self.config.label_in_progress)
        self.github.add_label(context.number, self.config.label_blocked)

        new_assignees = [a for a in context.assignees if a != self.config.bot_assignee]
        if self.config.human_reviewer and self.config.human_reviewer not in new_assignees:
            new_assignees.append(self.config.human_reviewer)
        self.github.set_assignees(context.number, new_assignees)

        print(f"  BLOCKED: {question}")
        return result

    def _handle_error(
        self,
        result: ProcessingResult,
        context: IssueContext,
        error: str,
    ) -> ProcessingResult:
        result.status = ProcessingStatus.FAILED
        result.error_message = error
        result.completed_at = datetime.now()

        comment = f"""**AutoClaude Failed** | Session: `{self.session_id}`

Processing failed with an error:

```
{error}
```

Please investigate and remove the `{self.config.label_blocked}` label to allow a retry."""

        self.github.add_comment(context.number, comment)
        self.github.remove_label(context.number, self.config.label_in_progress)
        self.github.add_label(context.number, self.config.label_failed)

        new_assignees = [a for a in context.assignees if a != self.config.bot_assignee]
        if self.config.human_reviewer and self.config.human_reviewer not in new_assignees:
            new_assignees.append(self.config.human_reviewer)
        self.github.set_assignees(context.number, new_assignees)

        print(f"  FAILED: {error}")
        return result

    async def _wait_for_ci(
        self,
        branch: str,
        context: IssueContext,
        result: ProcessingResult,
    ) -> bool:
        """Wait for CI to complete, attempt fixes if needed. Returns True if CI passed."""
        ci_attempts = 0
        start_time = time.time()

        while ci_attempts < self.config.max_ci_retries:
            while True:
                elapsed = time.time() - start_time
                if elapsed > self.config.ci_timeout:
                    self._handle_blocked(result, context, "CI timed out waiting for checks to complete")
                    return False

                status = self.github.get_ci_status(branch)
                result.ci_status = status.conclusion or status.status

                if not status.is_pending:
                    break

                print(f"    CI status: {status.status}... waiting")
                await asyncio.sleep(self.config.ci_poll_interval)

            if status.is_success:
                print(f"    CI passed!")
                return True

            if status.is_failure:
                ci_attempts += 1
                print(f"    CI failed (attempt {ci_attempts}/{self.config.max_ci_retries})")

                if ci_attempts >= self.config.max_ci_retries:
                    self._handle_blocked(
                        result,
                        context,
                        f"CI continues to fail after {ci_attempts} fix attempts. Failure: {status.failure_summary()}"
                    )
                    return False

                print(f"    Attempting to fix CI failures...")
                fix_result = await self.agent.run_fix_ci(context, status.failure_summary())

                if fix_result.blocked:
                    self._handle_blocked(result, context, fix_result.blocking_question)
                    return False

                if fix_result.error:
                    self._handle_error(result, context, fix_result.error)
                    return False

                if self.git.has_uncommitted_changes():
                    sha = self.git.commit("Fix CI failures", context.number)
                    if sha:
                        result.commits.append(sha)
                    self.git.push_branch(branch)

                start_time = time.time()

        return False

    def _create_pr(self, context: IssueContext, branch: str) -> tuple[int, str]:
        title = f"{context.title} (#{context.number})"

        body = f"""## Summary

Automated implementation for #{context.number}.

## Changes

See commit history for details.

## Test Plan

- [ ] CI passes
- [ ] Manual review of changes

---

Fixes #{context.number}

Generated by AutoClaude"""

        return self.github.create_pull_request(
            title=title,
            body=body,
            head=branch,
            base="main",
            draft=self.config.create_draft_pr,
        )

    def _mark_completed(self, issue_number: int, pr_url: str) -> None:
        comment = f"""Processing complete!

Pull request created: {pr_url}

The PR is ready for review."""

        self.github.add_comment(issue_number, comment)
        self.github.remove_label(issue_number, self.config.label_in_progress)
        self.github.add_label(issue_number, self.config.label_completed)
