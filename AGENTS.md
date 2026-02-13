# AutoClaude — Agent & Developer Guide

## Architecture

AutoClaude is a Python CLI (`autoclaude`) that autonomously processes GitHub issues and Azure DevOps work items by spawning Claude Agent SDK sessions. The pipeline is:

```
CLI (cli.py) → TicketProcessor (processor.py) → AgentRunner (agent.py)
                     ↓                               ↓
               TicketPlatform (platform.py)      Claude Agent SDK
               ├─ GitHubClient (github_client.py)  Permission Guard (permission_guard.py)
               └─ AdoClient    (ado_client.py)
               GitOperations   (github_client.py)
                     ↓
              IterationLoop (loop.py) — multi-pass fresh-context iterations
              Orchestrator  (orchestrator.py) — multi-repo coordination
              DAGProcessor  (dag.py) — dependency-aware parallel batch + merge queue
```

### Key modules

| Module | Purpose |
|--------|---------|
| `cli.py` | Argument parsing, env validation, command dispatch |
| `config.py` | `AutoClaudeConfig` dataclass — all settings in one place |
| `processor.py` | `TicketProcessor` — full issue lifecycle (claim → branch → agent → quality → CI → PR) |
| `agent.py` | `AgentRunner` — builds prompts, runs Claude Agent SDK, parses output signals |
| `platform.py` | `TicketPlatform` protocol + `WorkItem` — platform abstraction layer |
| `github_client.py` | `GitHubClient` (API via PyGithub) + `GitOperations` (subprocess git) |
| `ado_client.py` | `AdoClient` (Azure DevOps via `az` CLI) — implements `TicketPlatform` |
| `dag.py` | `DAGProcessor` + `MergeQueue` — dependency-aware batch processing with merge queue |
| `loop.py` | `IterationLoop` — multi-iteration and PRD batch modes |
| `orchestrator.py` | `Orchestrator` — cross-repo coordination with dependency ordering |
| `context.py` | Auto-discovers and loads project context files (AGENTS.md, CLAUDE.md, etc.) |
| `progress.py` | Append-only progress log, signal parsing (`is_complete`, `is_blocked`, `extract_learnings`) |
| `quality.py` | Quality gate — discovers checks from `.autoclaude/quality.yaml`, runs them, reports failures |
| `permission_guard.py` | PreToolUse hooks — auto-approve safe tools, block dangerous bash/writes |
| `models.py` | Shared data models (`IssueContext`, `ProcessingResult`, `CIStatus`, etc.) |

## Development

### Setup

```bash
uv pip install -e ".[dev]"
```

### Tests

```bash
pytest
```

All tests are in `tests/`. They're fast (no network, no subprocess) and use `tmp_path` fixtures.

### Code style

- Python 3.11+, type hints throughout
- Dataclasses over dicts for structured data
- `subprocess.run` for git commands (via `GitOperations._run_git`)
- PyGithub for GitHub API, `az` CLI for Azure DevOps
- No global state — config flows through constructors

## Platform abstraction

`TicketPlatform` (in `platform.py`) is a `Protocol` that both `GitHubClient` and `AdoClient` implement. `TicketProcessor` works with either platform via this protocol — selected by `config.platform` (`"github"` or `"azuredevops"`).

`WorkItem` is a platform-agnostic wrapper (`number`, `title`, `raw`) returned by all platform methods. The `raw` field holds the platform-specific object (GitHub `Issue`, ADO work item dict).

## Authentication

Default: OAuth via `claude login` session (Max plan tokens). Both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are stripped from the environment unless `--use-api-key` is passed.

## Agent signals protocol

The spawned Claude agent communicates back via structured text markers in its output:

| Signal | Purpose |
|--------|---------|
| `AUTOCLAUDE_COMPLETE` | Agent finished implementation |
| `AUTOCLAUDE_BLOCKED: <question>` | Agent needs human input (parsed by `_parse_agent_output`) |
| `AUTOCLAUDE_SUMMARY: <text>` | Used for commit message and PR body |
| `LEARNED: <insight>` | Captured into `.autoclaude/progress.md` |

**Important**: Only `AUTOCLAUDE_BLOCKED:` is matched for blocked detection (not bare `BLOCKED:`), to avoid false positives from file contents in agent output.

## Git remote configuration

`GitOperations` uses `config.git_remote` (CLI: `--remote`, default: `origin`) for all fetch/push/diff operations. This allows working with fork-based workflows where `origin` may point to an upstream repo rather than the user's fork.

## Permission guard

Uses Claude Agent SDK PreToolUse hooks (not `bypassPermissions`), so it works with both API key and OAuth auth. See `permission_guard.py` for the full blocked-patterns list.

## Quality gates

Checks are discovered from `.autoclaude/quality.yaml` and/or `--quality-check` CLI flags. On failure, the output is fed back to a fresh agent session for fixing, up to `--max-quality-retries` attempts.
