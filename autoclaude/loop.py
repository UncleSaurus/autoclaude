"""Iteration engine for AutoClaude.

Supports two modes:
- Per-issue iteration: Multiple fresh-context passes on a single issue.
- PRD batch loop: Work through a task list, one story per iteration.

Each iteration spawns a fresh Claude Agent SDK session with clean context.
Learnings carry forward via .autoclaude/progress.md and git history.
"""

import json
from pathlib import Path
from typing import Optional

from .agent import AgentResult, AgentRunner
from .config import AutoClaudeConfig
from .context import load_context, load_progress_context
from .github_client import GitOperations
from .models import IssueContext
from .progress import append_run, extract_learnings, is_blocked, is_complete


class IterationLoop:
    """Fresh-context iteration engine."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config
        self.agent = AgentRunner(config)
        self.git = GitOperations(config)

    def _get_context_root(self) -> str:
        """Get the root directory for context loading."""
        return self.config.context_dir or self.config.worktree_path or "."

    def _build_iteration_context(
        self,
        base_context: str,
        iteration: int,
        max_iterations: int,
        prior_learnings: list[str],
        git_diff_summary: str,
    ) -> str:
        """Build the full context for a single iteration.

        Combines project context with iteration-specific information so the
        fresh Claude instance knows what happened in prior iterations.
        """
        sections = [base_context] if base_context else []

        if iteration > 1:
            iter_lines = [
                "# Iteration Context",
                "",
                f"This is iteration {iteration} of {max_iterations}.",
                "Previous iterations have already made progress on this issue.",
                "",
            ]

            if git_diff_summary:
                iter_lines.extend([
                    "## Changes Made So Far",
                    "```",
                    git_diff_summary,
                    "```",
                    "",
                ])

            if prior_learnings:
                iter_lines.extend([
                    "## Learnings from Prior Iterations",
                ])
                for learning in prior_learnings:
                    iter_lines.append(f"- {learning}")
                iter_lines.append("")

            iter_lines.extend([
                "## Your Task",
                "",
                "Continue where the previous iteration left off. "
                "Review the git history and changes to understand what's been done, "
                "then complete any remaining work.",
                "",
            ])

            sections.append("\n".join(iter_lines))

        return "\n\n".join(sections)

    async def run_issue_loop(
        self,
        context: IssueContext,
        max_iterations: Optional[int] = None,
    ) -> AgentResult:
        """Run per-issue iteration with fresh context each pass.

        Args:
            context: The GitHub issue to process.
            max_iterations: Override config max_iterations.

        Returns:
            The result from the final iteration.
        """
        max_iter = max_iterations or self.config.max_iterations
        context_root = self._get_context_root()

        all_learnings: list[str] = []
        last_result: Optional[AgentResult] = None

        for iteration in range(1, max_iter + 1):
            print(f"  Iteration {iteration}/{max_iter}")

            # Load fresh context each iteration
            project_context = ""
            if not self.config.no_context:
                project_context = load_context(context_root)
                progress_context = load_progress_context(context_root)
                if progress_context:
                    project_context = project_context + "\n" + progress_context if project_context else progress_context

            # Get git diff for iteration awareness
            git_diff = ""
            if iteration > 1:
                git_diff = self.git.get_diff_summary()

            # Build iteration-aware context
            full_context = self._build_iteration_context(
                base_context=project_context,
                iteration=iteration,
                max_iterations=max_iter,
                prior_learnings=all_learnings,
                git_diff_summary=git_diff,
            )

            # Run fresh agent session
            result = await self.agent.run(context, project_context=full_context)
            last_result = result

            # Extract learnings from this iteration
            learnings = extract_learnings(result.output)
            all_learnings.extend(learnings)

            # Record progress
            status = "COMPLETED" if result.success else ("BLOCKED" if result.blocked else "FAILED")
            append_run(
                root=context_root,
                issue_number=context.number,
                title=context.title,
                status=status,
                learnings=learnings,
                iteration=iteration,
            )

            # Check termination conditions
            if is_complete(result.output):
                print(f"  Agent signaled completion at iteration {iteration}")
                break

            if is_blocked(result.output):
                print(f"  Agent blocked at iteration {iteration}: {result.blocking_question}")
                break

            if result.error:
                print(f"  Agent error at iteration {iteration}: {result.error}")
                break

            if not result.success:
                print(f"  Agent failed at iteration {iteration}")
                break

        return last_result or AgentResult(success=False, error="No iterations ran")

    async def run_batch_loop(
        self,
        prd_path: str,
        max_iterations: Optional[int] = None,
    ) -> list[AgentResult]:
        """Run PRD batch loop, one story per iteration.

        Args:
            prd_path: Path to prd.json file.
            max_iterations: Maximum total iterations across all stories.

        Returns:
            List of results, one per story attempted.
        """
        max_iter = max_iterations or 10
        prd = _load_prd(prd_path)
        context_root = self._get_context_root()

        results: list[AgentResult] = []
        iterations_used = 0

        for story in prd["stories"]:
            if story.get("done", False):
                continue

            if iterations_used >= max_iter:
                print(f"  Max iterations ({max_iter}) reached, stopping batch")
                break

            iterations_used += 1
            story_id = story.get("id", "?")
            story_title = story.get("title", "Untitled")
            print(f"\n  Story {story_id}: {story_title} (iteration {iterations_used}/{max_iter})")

            # Build context for this story
            project_context = ""
            if not self.config.no_context:
                project_context = load_context(context_root)
                progress_context = load_progress_context(context_root)
                if progress_context:
                    project_context = project_context + "\n" + progress_context if project_context else progress_context

            # Build a synthetic IssueContext for the story
            completed_stories = [s["title"] for s in prd["stories"] if s.get("done")]
            description = story.get("description", story_title)
            if completed_stories:
                description += "\n\n## Previously Completed Stories\n" + "\n".join(f"- {t}" for t in completed_stories)

            story_context = IssueContext(
                number=0,
                title=story_title,
                body=description,
                author="prd",
                labels=["batch"],
                assignees=[],
                comments=[],
                created_at=__import__("datetime").datetime.now(),
                updated_at=__import__("datetime").datetime.now(),
                url="",
            )

            result = await self.agent.run(story_context, project_context=project_context)
            results.append(result)

            # Extract learnings
            learnings = extract_learnings(result.output)

            # Record progress
            status = "COMPLETED" if is_complete(result.output) else ("BLOCKED" if result.blocked else "IN_PROGRESS")
            append_run(
                root=context_root,
                issue_number=0,
                title=f"[batch] {story_title}",
                status=status,
                learnings=learnings,
                iteration=iterations_used,
            )

            # Mark story done if complete
            if is_complete(result.output):
                story["done"] = True
                _save_prd(prd_path, prd)
                print(f"  Story {story_id} completed")
            elif is_blocked(result.output):
                print(f"  Story {story_id} blocked: {result.blocking_question}")
                break
            elif result.error:
                print(f"  Story {story_id} error: {result.error}")
                break

        return results


def _load_prd(path: str) -> dict:
    """Load a PRD file."""
    with open(path) as f:
        return json.load(f)


def _save_prd(path: str, prd: dict) -> None:
    """Save a PRD file (updates done flags)."""
    with open(path, "w") as f:
        json.dump(prd, f, indent=2)
        f.write("\n")
