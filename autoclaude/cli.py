#!/usr/bin/env python3
"""CLI entry point for AutoClaude."""

import argparse
import asyncio
import os
import sys

from pathlib import Path
from dotenv import load_dotenv

# Load .env from cwd at startup (before worktree changes cwd).
# Also check git repo root so worktree invocations find the main .env.
_cwd_env = Path.cwd() / ".env"
load_dotenv(_cwd_env)

import subprocess as _sp
try:
    _git_root = _sp.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if _git_root:
        _root_env = Path(_git_root) / ".env"
        if _root_env != _cwd_env.resolve():
            load_dotenv(_root_env, override=False)
except Exception:
    pass

from .config import AutoClaudeConfig
from .loop import IterationLoop
from .models import ProcessingStatus
from .orchestrator import Orchestrator, RepoConfig, RepoRelationship, create_multi_repo_orchestrator
from .processor import TicketProcessor


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AutoClaude - Autonomous GitHub issue processor powered by Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Claim command
    claim_parser = subparsers.add_parser(
        "claim",
        help="Claim and process issues/work items using labels/tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # GitHub
  autoclaude claim --repo owner/repo
  autoclaude claim --repo owner/repo --label bug
  autoclaude claim --repo owner/repo --issue 42

  # Azure DevOps
  autoclaude claim --platform azuredevops --ado-org MapLarge --ado-project "Data Science" --ado-repo my-repo
  autoclaude claim --platform azuredevops --issue 183
""",
    )
    _add_common_args(claim_parser)
    claim_parser.add_argument("--repo", help="GitHub repository (owner/repo)")
    claim_parser.add_argument("--issue", type=int, help="Specific issue/work item number to process")
    claim_parser.add_argument("--label", default="enhancement", help="Label/tag to filter issues (default: enhancement)")

    # Process command (legacy)
    process_parser = subparsers.add_parser(
        "process",
        help="Process issues assigned to bot (legacy, use 'claim' instead)",
    )
    _add_common_args(process_parser)
    process_parser.add_argument("--repo", help="GitHub repository (owner/repo)")
    process_parser.add_argument("--issue", type=int, help="Specific issue number")

    # Batch command (NEW)
    batch_parser = subparsers.add_parser(
        "batch",
        help="Process a PRD task list (one story per iteration)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  autoclaude batch --prd prd.json
  autoclaude batch --prd prd.json --max-iterations 20

PRD format (prd.json):
  {
    "stories": [
      {"id": "1", "title": "Add auth", "description": "...", "done": false},
      {"id": "2", "title": "Add tests", "description": "...", "done": false}
    ]
  }
""",
    )
    _add_common_args(batch_parser)
    batch_parser.add_argument("--prd", required=True, help="Path to prd.json file")

    # Orchestrate command
    orch_parser = subparsers.add_parser(
        "orchestrate",
        help="Orchestrate processing across multiple repositories",
    )
    _add_common_args(orch_parser)
    orch_parser.add_argument("--upstream", dest="upstream_repo", required=True, help="Upstream repository")
    orch_parser.add_argument("--downstream", dest="downstream_repo", default=None, help="Downstream repository")
    orch_parser.add_argument("--repo", choices=["upstream", "downstream", "all"], default="all")
    orch_parser.add_argument("--issue", type=int, help="Specific issue (requires --repo upstream/downstream)")

    # Multi command
    multi_parser = subparsers.add_parser(
        "multi",
        help="Orchestrate across custom repositories",
    )
    _add_common_args(multi_parser)
    multi_parser.add_argument("--repos", nargs="+", required=True, help="Repositories to process")
    multi_parser.add_argument("--upstream", nargs="*", default=[], help="Upstream repositories")

    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    # Platform selection
    parser.add_argument("--platform", choices=["github", "azuredevops"], default="github",
                        help="Ticket platform (default: github)")

    # ADO-specific
    parser.add_argument("--ado-org", default=os.environ.get("ADO_ORG", ""),
                        help="Azure DevOps organization (env: ADO_ORG)")
    parser.add_argument("--ado-project", default=os.environ.get("ADO_PROJECT", ""),
                        help="Azure DevOps project (env: ADO_PROJECT)")
    parser.add_argument("--ado-repo", default=os.environ.get("ADO_REPO", ""),
                        help="Azure DevOps repository name (env: ADO_REPO)")

    # Common args
    parser.add_argument("--assignee", default=os.environ.get("GITHUB_BOT_ASSIGNEE", "claude-bot"))
    parser.add_argument("--reviewer", default=os.environ.get("GITHUB_HUMAN_REVIEWER", ""))
    parser.add_argument("--model", choices=["opus", "sonnet", "haiku"], default="sonnet",
                        help="Claude model (default: sonnet)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--max-turns", type=int, default=50,
                        help="Max agent turns per iteration (default: 50)")
    parser.add_argument("--max-iterations", type=int, default=1,
                        help="Max iterations per issue (default: 1, set higher for complex issues)")
    parser.add_argument("--worktree", action="store_true", help="Use isolated git worktree per issue")
    parser.add_argument("--worktree-base", default="../", help="Base path for worktrees (default: ../)")
    parser.add_argument("--repo-dir", default=None, help="Local path to target repository (default: cwd)")
    parser.add_argument("--context-dir", default=None, help="Override context discovery directory")
    parser.add_argument("--no-context", action="store_true", help="Skip context loading (AGENTS.md, etc.)")
    parser.add_argument("--skip-clarification", action="store_true",
                        help="Skip issue analysis/clarification phase")
    parser.add_argument("--remote", default="origin", help="Git remote for fetch/push (default: origin)")
    parser.add_argument("--base-branch", default="main", help="Base branch name (default: main)")
    parser.add_argument("--verbose", action="store_true", help="Stream agent actions to terminal in real-time")
    parser.add_argument("--use-api-key", action="store_true",
                        help="Use ANTHROPIC_API_KEY for billing instead of OAuth/Max plan tokens")
    parser.add_argument("--cli-path", default=None,
                        help="Path to claude CLI binary (default: auto-detect native install)")
    parser.add_argument(
        "--quality-check", action="append", default=[], dest="quality_checks",
        help="Shell command to run as quality gate after agent (repeatable)",
    )
    parser.add_argument(
        "--max-quality-retries", type=int, default=2,
        help="Max attempts to fix quality failures (default: 2)",
    )


MODEL_IDS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}


def _detect_native_claude_cli() -> str | None:
    """Auto-detect the native Claude CLI installed by Claude desktop app."""
    base = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"
    if not base.exists():
        return None
    # Find latest version directory
    versions = sorted(
        [d for d in base.iterdir() if d.is_dir() and (d / "claude").exists()],
        key=lambda d: d.name,
        reverse=True,
    )
    if versions:
        return str(versions[0] / "claude")
    return None


def validate_env(platform: str = "github", *, use_api_key: bool = False) -> tuple[str, str]:
    """Validate environment and return (github_token, anthropic_key).

    Default: strips ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN so the SDK
    falls back to the active `claude login` session (Max plan tokens).
    Pass use_api_key=True to keep the API key for billing.
    """
    github_token = os.environ.get("GITHUB_TOKEN", "")

    errors = []
    if platform == "github" and not github_token:
        errors.append("GITHUB_TOKEN environment variable is required for GitHub platform")

    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    if use_api_key:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            print("Warning: --use-api-key specified but ANTHROPIC_API_KEY not set", file=sys.stderr)
        else:
            print("Using ANTHROPIC_API_KEY for billing", file=sys.stderr)
        return github_token, anthropic_key

    # Default: strip API key AND auth token so the SDK falls back to
    # the active `claude login` session (Max plan tokens).
    # Without this, a stale ANTHROPIC_AUTH_TOKEN from .env would override
    # the live OAuth session.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    print("Using OAuth/Max plan auth (pass --use-api-key to use API billing)", file=sys.stderr)
    return github_token, ""


def make_config(args: argparse.Namespace, github_token: str, anthropic_key: str, repo: str = "") -> AutoClaudeConfig:
    """Build an AutoClaudeConfig from CLI args."""
    # Resolve CLI path: explicit flag > auto-detect native > SDK default (PATH lookup)
    cli_path = getattr(args, "cli_path", None)
    if not cli_path:
        cli_path = _detect_native_claude_cli()
        if cli_path:
            print(f"Auto-detected native Claude CLI: {cli_path}", file=sys.stderr)

    return AutoClaudeConfig(
        platform=args.platform,
        github_token=github_token,
        anthropic_api_key=anthropic_key,
        repo=repo or getattr(args, "repo", "") or "",
        bot_assignee=args.assignee,
        human_reviewer=args.reviewer,
        model=MODEL_IDS[args.model],
        max_turns=args.max_turns,
        max_iterations=args.max_iterations,
        dry_run=args.dry_run,
        git_remote=getattr(args, "remote", "origin"),
        base_branch=getattr(args, "base_branch", "main"),
        repo_dir=getattr(args, "repo_dir", None),
        use_worktree=args.worktree,
        worktree_base_path=args.worktree_base,
        context_dir=args.context_dir,
        no_context=args.no_context,
        skip_clarification=getattr(args, "skip_clarification", False),
        verbose=getattr(args, "verbose", False),
        quality_checks=getattr(args, "quality_checks", []),
        max_quality_retries=getattr(args, "max_quality_retries", 2),
        ado_org=args.ado_org,
        ado_project=args.ado_project,
        ado_repo=args.ado_repo,
        cli_path=cli_path,
    )


async def cmd_claim(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env(args.platform, use_api_key=args.use_api_key)
    config = make_config(args, github_token, anthropic_key)
    config.issue_number = args.issue

    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    processor = TicketProcessor(config)
    target = config.repo or f"{config.ado_org}/{config.ado_project}"
    print(f"Session: {processor.session_id}")
    if config.max_iterations > 1:
        print(f"Max iterations: {config.max_iterations}")

    if config.issue_number:
        if processor.github.is_claimed(config.issue_number):
            print(f"Issue #{config.issue_number} is already claimed")
            return 1
        print(f"Claiming issue #{config.issue_number} in {target}...")
        result = await processor.process_single(config.issue_number)
        results = [result]
    else:
        print(f"Finding claimable issues in {target} with label '{args.label}'...")
        results = await processor.process_claimable(args.label)

    _print_summary(results)
    return 1 if any(r.status == ProcessingStatus.FAILED for r in results) else 0


async def cmd_process(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env(args.platform, use_api_key=args.use_api_key)
    config = make_config(args, github_token, anthropic_key)
    config.issue_number = args.issue

    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    processor = TicketProcessor(config)
    target = config.repo or f"{config.ado_org}/{config.ado_project}"

    if config.issue_number:
        print(f"Processing issue #{config.issue_number} in {target}...")
        result = await processor.process_single(config.issue_number)
        results = [result]
    else:
        print(f"Processing all assigned issues in {target}...")
        results = await processor.process_all_assigned()

    _print_summary(results)
    return 1 if any(r.status == ProcessingStatus.FAILED for r in results) else 0


async def cmd_batch(args: argparse.Namespace) -> int:
    """Handle the 'batch' command for PRD-based processing."""
    github_token, anthropic_key = validate_env(args.platform, use_api_key=args.use_api_key)
    config = make_config(args, github_token, anthropic_key, repo="")

    loop = IterationLoop(config)
    print(f"Running batch from {args.prd} (max {args.max_iterations} iterations)...")

    results = await loop.run_batch_loop(args.prd, max_iterations=args.max_iterations)

    completed = sum(1 for r in results if r.success)
    print(f"\nBatch complete: {completed}/{len(results)} stories succeeded")

    return 0 if all(r.success for r in results) else 1


async def cmd_orchestrate(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env(args.platform, use_api_key=args.use_api_key)

    orchestrator = create_multi_repo_orchestrator(
        github_token=github_token,
        anthropic_api_key=anthropic_key,
        upstream_repo=args.upstream_repo,
        downstream_repo=args.downstream_repo,
        assignee=args.assignee,
        reviewer=args.reviewer,
        dry_run=args.dry_run,
    )

    if args.issue:
        if args.repo == "all":
            print("Error: --issue requires --repo to be 'upstream' or 'downstream'", file=sys.stderr)
            return 1
        repo = args.upstream_repo if args.repo == "upstream" else args.downstream_repo
        if not repo:
            print("Error: downstream repo not specified", file=sys.stderr)
            return 1
        result = await orchestrator.process_issue(repo, args.issue)
    elif args.repo == "all":
        result = await orchestrator.process_all()
    else:
        repo = args.upstream_repo if args.repo == "upstream" else args.downstream_repo
        if not repo:
            print("Error: downstream repo not specified", file=sys.stderr)
            return 1
        result = await orchestrator.process_repo(repo)

    print(result.summary())

    for results in result.results_by_repo.values():
        if any(r.status == ProcessingStatus.FAILED for r in results):
            return 1
    return 0


async def cmd_multi(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env(args.platform, use_api_key=args.use_api_key)

    upstream_set = set(args.upstream)
    repos = []
    for repo in args.repos:
        if repo in upstream_set:
            repos.append(RepoConfig(
                repo=repo,
                relationship=RepoRelationship.UPSTREAM,
                downstream=[r for r in args.repos if r not in upstream_set],
            ))
        else:
            repos.append(RepoConfig(
                repo=repo,
                relationship=RepoRelationship.DOWNSTREAM if upstream_set else RepoRelationship.INDEPENDENT,
                depends_on=list(upstream_set),
            ))

    orchestrator = Orchestrator(
        repos=repos,
        github_token=github_token,
        anthropic_api_key=anthropic_key,
        default_assignee=args.assignee,
        default_reviewer=args.reviewer,
        model=MODEL_IDS[args.model],
        dry_run=args.dry_run,
    )

    result = await orchestrator.process_all()
    print(result.summary())

    for results in result.results_by_repo.values():
        if any(r.status == ProcessingStatus.FAILED for r in results):
            return 1
    return 0


def _print_summary(results: list) -> None:
    print("\n" + "=" * 50)
    print("PROCESSING SUMMARY")
    print("=" * 50)

    completed = sum(1 for r in results if r.status == ProcessingStatus.COMPLETED)
    blocked = sum(1 for r in results if r.status == ProcessingStatus.BLOCKED)
    failed = sum(1 for r in results if r.status == ProcessingStatus.FAILED)

    print(f"Total: {len(results)} | Completed: {completed} | Blocked: {blocked} | Failed: {failed}")

    for result in results:
        print(f"\n  #{result.issue_number}: {result.status.value}")
        if result.pr_url:
            print(f"    PR: {result.pr_url}")
        if result.blocking_question:
            print(f"    Blocked: {result.blocking_question}")
        if result.error_message:
            print(f"    Error: {result.error_message}")


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Load .env from --repo-dir so tokens are available without shell hacks.
    repo_dir = getattr(args, "repo_dir", None)
    if repo_dir:
        load_dotenv(Path(repo_dir) / ".env", override=False)

    if args.dry_run:
        print("DRY RUN MODE\n")

    commands = {
        "claim": cmd_claim,
        "process": cmd_process,
        "batch": cmd_batch,
        "orchestrate": cmd_orchestrate,
        "multi": cmd_multi,
    }

    handler = commands.get(args.command)
    if handler:
        exit_code = asyncio.run(handler(args))
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
