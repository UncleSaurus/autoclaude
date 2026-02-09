"""Claude Agent SDK integration for autonomous code execution."""

import re
import sys
from dataclasses import dataclass
from typing import Optional

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from .config import AutoClaudeConfig
from .models import ClarificationOption, ClarificationQuestion, ClarificationRequest, IssueContext
from .permission_guard import build_hooks
from .progress import is_complete


@dataclass
class AgentResult:
    """Result from agent execution."""

    success: bool
    blocked: bool = False
    blocking_question: Optional[str] = None
    error: Optional[str] = None
    session_id: Optional[str] = None
    output: str = ""


@dataclass
class AnalysisResult:
    """Result from issue analysis phase."""

    ready_to_implement: bool
    clarification_request: Optional[ClarificationRequest] = None
    implementation_plan: Optional[str] = None
    error: Optional[str] = None


class AgentRunner:
    """Runs Claude Agent SDK for autonomous code execution."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config

    def _log_verbose(self, message) -> None:
        """Print agent message to stderr if verbose mode is on."""
        if not self.config.verbose:
            return

        # AssistantMessage — text output and tool calls
        if hasattr(message, "content") and isinstance(message.content, list):
            for block in message.content:
                if hasattr(block, "text"):
                    print(f"\033[36m[agent]\033[0m {block.text}", file=sys.stderr)
                elif hasattr(block, "name") and hasattr(block, "input"):
                    tool_input = block.input
                    # Truncate large inputs
                    preview = str(tool_input)
                    if len(preview) > 200:
                        preview = preview[:200] + "..."
                    print(f"\033[33m[tool]\033[0m {block.name}({preview})", file=sys.stderr)

        # ResultMessage — final summary
        elif hasattr(message, "num_turns") and hasattr(message, "duration_ms"):
            secs = message.duration_ms / 1000
            cost = f" ${message.total_cost_usd:.4f}" if message.total_cost_usd else ""
            print(f"\033[32m[done]\033[0m {message.num_turns} turns, {secs:.1f}s{cost}", file=sys.stderr)

    def _build_prompt(self, context: IssueContext) -> str:
        """Build the prompt for Claude from issue context.

        Project context (AGENTS.md, CLAUDE.md, etc.) is injected via the system
        prompt using SystemPromptPreset, not prepended here.

        Args:
            context: The GitHub issue context.
        """
        sections = []

        sections.append(f"""You are an autonomous coding agent processing a GitHub issue. Your task is to fully implement the requested changes.

{context.format_for_prompt()}

## Instructions

1. **Understand the requirements**: Read the issue carefully — every word matters. If file paths are mentioned, read those files FIRST before making any changes. Understand what the code currently does and what the issue is asking you to change.

2. **Implement the changes**: Make all necessary code changes to fulfill the request. This may involve:
   - Reading existing code to understand the codebase
   - Creating new files
   - Modifying existing files
   - Running commands to test your changes

3. **Run tests**: After making changes, run the project's test suite. If there's a test command in the project context (AGENTS.md, CLAUDE.md), use it. Fix any test failures before proceeding.

4. **Verify your work before signaling completion**:
   - Run `git diff` to review ALL your changes
   - Re-read the issue requirements and confirm your changes match what was asked
   - If the issue says "replace X with Y", verify you replaced X with Y (not the reverse)
   - If the issue says "extract/move code to a new location", verify the code exists in the new location
   - If you made no meaningful code changes, do NOT signal completion

5. **Do NOT commit**: Do not run `git add` or `git commit`. The orchestrator handles commits after your changes are verified.

6. **Handle blocking issues**: If you genuinely cannot proceed without human input:
   - Output exactly: `AUTOCLAUDE_BLOCKED: <your specific question>`
   - Only do this if you truly cannot make progress
   - Be specific about what information you need

7. **Signal completion**: ONLY after verifying your changes match the requirements:
   - First output a summary: `AUTOCLAUDE_SUMMARY: <1-2 sentence description of what you changed and why>`
   - Then output exactly: `AUTOCLAUDE_COMPLETE`
   - Never signal completion if you made no code changes
   - Never signal completion without first reviewing your diff

8. **Share learnings**: If you discover something useful about the codebase:
   - Output: `LEARNED: <insight>` for each discovery
   - These get saved for future runs

9. **Quality standards**:
   - Follow existing code patterns and style
   - Don't introduce security vulnerabilities
   - Keep changes focused - don't refactor unrelated code
   - Don't add unnecessary comments or documentation

## Important

- Work autonomously. Make decisions and implement changes without asking for permission.
- You have full access to read files, write files, edit files, and run bash commands.
- If tests fail, try to fix them. Only report as blocked if you've tried and can't fix.
- Your changes will be committed, pushed, and CI will run automatically after you finish.
- CRITICAL: Double-check the direction of your changes. If the issue says "use config function instead of hardcoded value", make sure you're adding the config function call, not removing it.

Begin by reading any referenced files to understand the current state, then implement the requested changes.""")

        return "\n\n".join(sections)

    async def run(self, context: IssueContext, project_context: str = "") -> AgentResult:
        """Run the agent to process an issue.

        Args:
            context: The GitHub issue context.
            project_context: Pre-loaded project context injected via system prompt.
        """
        prompt = self._build_prompt(context)

        # Inject project context via system prompt (appended to Claude Code defaults).
        # Using the preset preserves Claude Code's built-in system prompt.
        # Without this, system_prompt=None causes --system-prompt "" which wipes defaults.
        system_prompt: dict = {"type": "preset", "preset": "claude_code"}
        if project_context:
            system_prompt["append"] = project_context

        options = ClaudeAgentOptions(
            allowed_tools=[
                "Read", "Write", "Edit", "Bash",
                "Glob", "Grep", "WebSearch", "WebFetch",
            ],
            model=self.config.model,
            max_turns=self.config.max_turns,
            cwd=self.config.worktree_path,
            hooks=build_hooks(),
            system_prompt=system_prompt,
            setting_sources=["user", "project", "local"],
        )
        return await self._run_client(prompt, options)

    async def analyze_issue(self, context: IssueContext) -> AnalysisResult:
        """Analyze an issue to determine if clarification is needed."""
        prompt = f"""You are analyzing a GitHub issue to determine if it's ready for implementation or needs clarification first.

{context.format_for_prompt()}

## Your Task

Analyze this issue and determine:

1. **Is the issue clear enough to implement?** Consider:
   - Are the requirements specific and unambiguous?
   - Is the scope well-defined?
   - Are there multiple valid approaches that need a decision?
   - Are there missing details that would block implementation?

2. **If clarification is needed**, identify 1-4 specific questions. For each question:
   - Give it a short ID (e.g., SCOPE, APPROACH, DEPENDENCY)
   - Provide 2-4 concrete options when possible
   - Be specific, not vague

## Output Format

If READY to implement, output:
```
READY_TO_IMPLEMENT

## Implementation Plan
[Brief plan of what you would do]
```

If NEEDS CLARIFICATION, output:
```
NEEDS_CLARIFICATION

QUESTION[ID]: <question text>
- OPTION: <option 1>
- OPTION: <option 2>
[repeat for each question, max 4 questions]
```

Analyze the issue now and provide your assessment.
"""

        try:
            options = ClaudeAgentOptions(
                allowed_tools=["Read", "Glob", "Grep"],
                model=self.config.model,
                max_turns=10,
                cwd=self.config.worktree_path,
                hooks=build_hooks(),
                system_prompt={"type": "preset", "preset": "claude_code"},
                setting_sources=["user", "project", "local"],
            )

            result = await self._run_client(prompt, options)
            full_output = result.output
            full_output = full_output.replace("\\n", "\n")

            if "READY_TO_IMPLEMENT" in full_output:
                plan_match = re.search(r"## Implementation Plan\s*\n(.+?)(?:\n```|$)", full_output, re.DOTALL)
                plan = plan_match.group(1).strip() if plan_match else "Implementation plan not provided"
                return AnalysisResult(ready_to_implement=True, implementation_plan=plan)

            elif "NEEDS_CLARIFICATION" in full_output:
                questions_dict: dict[str, ClarificationQuestion] = {}
                question_pattern = r"QUESTION\[(\w+)\]:\s*(.+?)(?=QUESTION\[|$)"
                for match in re.finditer(question_pattern, full_output, re.DOTALL):
                    q_id = match.group(1)
                    if q_id in questions_dict:
                        continue

                    q_block = match.group(2).strip()
                    q_lines = q_block.split("\n")
                    q_text = q_lines[0].strip()

                    options_list = []
                    for line in q_lines[1:]:
                        opt_match = re.match(r"- OPTION:\s*(.+)", line.strip())
                        if opt_match:
                            opt_text = opt_match.group(1).strip()
                            opt_text = re.sub(r'[\"\'\]]+$', '', opt_text).strip()
                            open_count = opt_text.count('(')
                            close_count = opt_text.count(')')
                            while close_count > open_count and opt_text.endswith(')'):
                                opt_text = opt_text[:-1].strip()
                                close_count -= 1
                            if opt_text:
                                options_list.append(ClarificationOption(label=opt_text))

                    questions_dict[q_id] = ClarificationQuestion(
                        id=q_id,
                        question=q_text,
                        options=options_list,
                    )

                questions = list(questions_dict.values())
                if questions:
                    return AnalysisResult(
                        ready_to_implement=False,
                        clarification_request=ClarificationRequest(questions=questions),
                    )

            return AnalysisResult(ready_to_implement=True, implementation_plan="Analysis inconclusive, proceeding with implementation")

        except Exception as e:
            return AnalysisResult(ready_to_implement=False, error=str(e))

    async def run_fix_quality(self, context: IssueContext, failure_summary: str) -> AgentResult:
        """Run the agent to fix quality check failures."""
        prompt = f"""Quality checks failed after your implementation for issue #{context.number}. Fix the failures.

## Quality Check Failures

{failure_summary}

## Original Issue
{context.format_for_prompt()}

## Instructions

1. Read the failure output carefully
2. Fix the issues in the code (test failures, lint errors, type errors, etc.)
3. Do NOT commit — the orchestrator handles commits
4. Only fix what the quality checks report — don't refactor unrelated code
5. If you cannot fix an issue, explain why

When done, output: `AUTOCLAUDE_COMPLETE`

If you genuinely cannot fix the issue:
`AUTOCLAUDE_BLOCKED: <description of what's failing and what you tried>`

Begin by understanding the failures, then implement fixes.
"""
        return await self._run_with_prompt(prompt)

    async def run_fix_ci(self, context: IssueContext, failure_summary: str) -> AgentResult:
        """Run the agent to fix CI failures."""
        prompt = f"""The CI pipeline failed for issue #{context.number}. Fix the failures.

## CI Failure Summary
{failure_summary}

## Original Issue
{context.format_for_prompt()}

## Instructions

1. Analyze the CI failure output
2. Identify what's broken (tests, linting, type errors, etc.)
3. Fix the issues in the code
4. Commit your fixes with message: "Fix CI failures (#{context.number})"

If you cannot fix the issue after reasonable attempts, output:
`AUTOCLAUDE_BLOCKED: <description of what's failing and what you tried>`

When done, output: `AUTOCLAUDE_COMPLETE`

Begin by understanding the failure, then implement fixes.
"""
        return await self._run_with_prompt(prompt)

    async def _run_with_prompt(self, prompt: str) -> AgentResult:
        """Run agent with a specific prompt."""
        options = ClaudeAgentOptions(
            allowed_tools=[
                "Read", "Write", "Edit", "Bash",
                "Glob", "Grep",
            ],
            model=self.config.model,
            max_turns=self.config.max_turns,
            cwd=self.config.worktree_path,
            hooks=build_hooks(),
            system_prompt={"type": "preset", "preset": "claude_code"},
            setting_sources=["user", "project", "local"],
        )
        return await self._run_client(prompt, options)

    async def _run_client(self, prompt: str, options: ClaudeAgentOptions) -> AgentResult:
        """Run agent using ClaudeSDKClient (required for can_use_tool callback)."""
        output_lines: list[str] = []
        session_id: Optional[str] = None

        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(prompt)

                async for message in client.receive_response():
                    self._log_verbose(message)

                    if hasattr(message, "subtype") and message.subtype == "init":
                        session_id = getattr(message, "session_id", None)

                    if hasattr(message, "result"):
                        output_lines.append(str(message.result))
                    elif hasattr(message, "content"):
                        output_lines.append(str(message.content))

            full_output = "\n".join(output_lines)
            return self._parse_agent_output(full_output, session_id)

        except Exception as e:
            # The subprocess may exit non-zero even after successful completion.
            # Check captured output for completion signal before assuming failure.
            full_output = "\n".join(output_lines)
            result = self._parse_agent_output(full_output, session_id)
            if result.success:
                return result
            return AgentResult(
                success=False,
                error=str(e),
                output=full_output,
            )

    @staticmethod
    def _parse_agent_output(full_output: str, session_id: Optional[str] = None) -> AgentResult:
        """Parse agent output for completion/blocked signals."""
        completed = is_complete(full_output)

        blocked_match = re.search(r"AUTOCLAUDE_BLOCKED:\s*(.+?)(?:\n|$)", full_output)
        blocked = blocked_match is not None
        blocking_question = blocked_match.group(1).strip()[:500] if blocked_match else None

        return AgentResult(
            success=completed or (not blocked),
            blocked=blocked,
            blocking_question=blocking_question,
            session_id=session_id,
            output=full_output,
        )
