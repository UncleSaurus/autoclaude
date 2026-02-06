#!/usr/bin/env python3
"""CLI entry point for AutoClaude."""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

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
        help="Claim and process issues using labels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  autoclaude claim --repo owner/repo
  autoclaude claim --repo owner/repo --label bug
  autoclaude claim --repo owner/repo --issue 42
  autoclaude claim --repo owner/repo --issue 42 --max-iterations 5
  autoclaude claim --repo owner/repo --issue 42 --worktree
  autoclaude claim --repo owner/repo --dry-run
""",
    )
    _add_common_args(claim_parser)
    claim_parser.add_argument("--repo", required=True, help="GitHub repository (owner/repo)")
    claim_parser.add_argument("--issue", type=int, help="Specific issue number to process")
    claim_parser.add_argument("--label", default="enhancement", help="Label to filter issues (default: enhancement)")

    # Process command (legacy)
    process_parser = subparsers.add_parser(
        "process",
        help="Process issues assigned to bot (legacy, use 'claim' instead)",
    )
    _add_common_args(process_parser)
    process_parser.add_argument("--repo", required=True, help="GitHub repository (owner/repo)")
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
    parser.add_argument("--assignee", default=os.environ.get("GITHUB_BOT_ASSIGNEE", "claude-bot"))
    parser.add_argument("--reviewer", default=os.environ.get("GITHUB_HUMAN_REVIEWER", ""))
    parser.add_argument("--model", choices=["opus", "sonnet", "haiku"], default="sonnet", help="Claude model (default: sonnet)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--max-turns", type=int, default=50, help="Max agent turns per iteration (default: 50)")
    parser.add_argument("--max-iterations", type=int, default=1, help="Max iterations per issue (default: 1, set higher for complex issues)")
    parser.add_argument("--worktree", action="store_true", help="Use isolated git worktree per issue")
    parser.add_argument("--worktree-base", default="../", help="Base path for worktrees (default: ../)")
    parser.add_argument("--context-dir", default=None, help="Override context discovery directory")
    parser.add_argument("--no-context", action="store_true", help="Skip context loading (AGENTS.md, etc.)")


MODEL_IDS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}


def validate_env() -> tuple[str, str]:
    github_token = os.environ.get("GITHUB_TOKEN", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    errors = []
    if not github_token:
        errors.append("GITHUB_TOKEN environment variable is required")
    if not anthropic_key:
        errors.append("ANTHROPIC_API_KEY environment variable is required")

    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    return github_token, anthropic_key


def make_config(args: argparse.Namespace, github_token: str, anthropic_key: str, repo: str = "") -> AutoClaudeConfig:
    """Build an AutoClaudeConfig from CLI args."""
    return AutoClaudeConfig(
        github_token=github_token,
        anthropic_api_key=anthropic_key,
        repo=repo or getattr(args, "repo", ""),
        bot_assignee=args.assignee,
        human_reviewer=args.reviewer,
        model=MODEL_IDS[args.model],
        max_turns=args.max_turns,
        max_iterations=args.max_iterations,
        dry_run=args.dry_run,
        use_worktree=args.worktree,
        worktree_base_path=args.worktree_base,
        context_dir=args.context_dir,
        no_context=args.no_context,
    )


async def cmd_claim(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env()
    config = make_config(args, github_token, anthropic_key)
    config.issue_number = args.issue

    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    processor = TicketProcessor(config)
    print(f"Session: {processor.session_id}")
    if config.max_iterations > 1:
        print(f"Max iterations: {config.max_iterations}")

    if config.issue_number:
        if processor.github.is_claimed(config.issue_number):
            print(f"Issue #{config.issue_number} is already claimed")
            return 1
        print(f"Claiming issue #{config.issue_number} in {args.repo}...")
        result = await processor.process_single(config.issue_number)
        results = [result]
    else:
        print(f"Finding claimable issues in {args.repo} with label '{args.label}'...")
        results = await processor.process_claimable(args.label)

    _print_summary(results)
    return 1 if any(r.status == ProcessingStatus.FAILED for r in results) else 0


async def cmd_process(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env()
    config = make_config(args, github_token, anthropic_key)
    config.issue_number = args.issue

    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    processor = TicketProcessor(config)

    if config.issue_number:
        print(f"Processing issue #{config.issue_number} in {args.repo}...")
        result = await processor.process_single(config.issue_number)
        results = [result]
    else:
        print(f"Processing all assigned issues in {args.repo}...")
        results = await processor.process_all_assigned()

    _print_summary(results)
    return 1 if any(r.status == ProcessingStatus.FAILED for r in results) else 0


async def cmd_batch(args: argparse.Namespace) -> int:
    """Handle the 'batch' command for PRD-based processing."""
    github_token, anthropic_key = validate_env()
    config = make_config(args, github_token, anthropic_key, repo="")

    loop = IterationLoop(config)
    print(f"Running batch from {args.prd} (max {args.max_iterations} iterations)...")

    results = await loop.run_batch_loop(args.prd, max_iterations=args.max_iterations)

    completed = sum(1 for r in results if r.success)
    print(f"\nBatch complete: {completed}/{len(results)} stories succeeded")

    return 0 if all(r.success for r in results) else 1


async def cmd_orchestrate(args: argparse.Namespace) -> int:
    github_token, anthropic_key = validate_env()

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
    github_token, anthropic_key = validate_env()

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
