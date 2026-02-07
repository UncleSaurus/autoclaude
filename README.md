# AutoClaude

Autonomous GitHub issue processor powered by Claude. Picks up issues, implements changes, runs quality checks, and opens PRs — all without human intervention.

## Install

```bash
pip install git+https://github.com/UncleSaurus/autoclaude.git
```

Or for local development:

```bash
uv pip install -e /path/to/autoclaude
```

## Environment Variables

```bash
GITHUB_TOKEN=ghp_...           # GitHub PAT with repo access (required)
ANTHROPIC_API_KEY=sk-ant-...   # Claude API key (optional if logged in via `claude login`)
GITHUB_BOT_ASSIGNEE=claude-bot # Bot username for claiming (optional)
GITHUB_HUMAN_REVIEWER=username # Human fallback for blocked issues (optional)
```

If `ANTHROPIC_API_KEY` is not set, the Claude CLI falls back to OAuth authentication (Max plan token from `claude login`).

## Quick Start

```bash
# Process a specific issue
autoclaude claim --repo owner/repo --issue 42

# Use isolated git worktree (recommended for parallel work)
autoclaude claim --repo owner/repo --issue 42 --worktree

# Multi-iteration for complex issues
autoclaude claim --repo owner/repo --issue 42 --max-iterations 5

# Use Opus model instead of default Sonnet
autoclaude claim --repo owner/repo --issue 42 --model opus

# Preview without making changes
autoclaude claim --repo owner/repo --dry-run

# Process all issues with a specific label
autoclaude claim --repo owner/repo --label bug
```

## Quality Gates

Run project-defined checks (tests, linters, etc.) after the agent implements changes. If checks fail, failures are fed back to the agent for fixing.

### Via CLI flags

```bash
autoclaude claim --repo owner/repo --issue 42 \
  --quality-check "pytest" \
  --quality-check "ruff check ."
```

### Via project config

Create `.autoclaude/quality.yaml` in your repository:

```yaml
checks:
  # Simple form: command string
  - pytest --unit
  - ruff check .

  # Named form: for clearer output
  - name: Type Check
    command: mypy src/
```

Checks are discovered automatically. CLI `--quality-check` flags are merged with project config.

### Retry behavior

When checks fail, the agent gets a fresh session with the failure output and attempts to fix the issues. This repeats up to `--max-quality-retries` times (default: 2). If checks still fail after retries, the commit proceeds with a note.

## Batch Mode (PRD)

Process a task list from a JSON file, one story per iteration:

```bash
autoclaude batch --prd prd.json --max-iterations 20
```

PRD format:

```json
{
  "stories": [
    {"id": "1", "title": "Add auth", "description": "Implement JWT auth", "done": false},
    {"id": "2", "title": "Add tests", "description": "Add unit tests for auth", "done": false}
  ]
}
```

Stories are marked `"done": true` automatically on completion.

## Multi-Repo Orchestration

Process issues across related repositories:

```bash
# Upstream/downstream pair
autoclaude orchestrate --upstream owner/core --downstream owner/app

# Custom multi-repo
autoclaude multi --repos owner/core owner/app owner/docs --upstream owner/core
```

## Project Context

AutoClaude automatically loads context files from the project root to give the agent codebase awareness:

- `AGENTS.md` — Agent-specific instructions and conventions
- `CLAUDE.md` — Claude Code instructions (also used by AutoClaude)
- `PROJECT_STATUS.md` — Current project state
- `README.md` — Project overview

Skip with `--no-context` or override the discovery root with `--context-dir`.

## How It Works

1. **Claim** — Labels issue `agent-claimed`, creates a branch
2. **Analyze** — Optionally checks if issue needs clarification first
3. **Implement** — Spawns Claude Agent SDK session with full tool access
4. **Quality gate** — Runs project-defined checks, feeds failures back for fixing
5. **Commit & push** — Commits changes (excluding `.autoclaude/`), pushes branch
6. **CI** — Waits for CI, attempts fixes if it fails (up to 3 retries)
7. **PR** — Opens pull request with descriptive summary
8. **Clean up** — Removes worktree, updates labels

## Agent Signals

The agent communicates back via structured output:

| Signal | Meaning |
|--------|---------|
| `AUTOCLAUDE_COMPLETE` | Implementation done |
| `AUTOCLAUDE_BLOCKED: <question>` | Needs human input |
| `AUTOCLAUDE_SUMMARY: <text>` | Used for commit message and PR body |
| `LEARNED: <insight>` | Saved to `.autoclaude/progress.md` for future runs |

## CLI Reference

```
autoclaude claim     # Claim and process issues (primary command)
autoclaude process   # Process assigned issues (legacy)
autoclaude batch     # PRD batch processing
autoclaude orchestrate  # Multi-repo upstream/downstream
autoclaude multi     # Custom multi-repo orchestration
```

Common flags (all commands):

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `sonnet` | Claude model: opus, sonnet, haiku |
| `--dry-run` | off | Preview without making changes |
| `--max-turns` | 50 | Max agent turns per iteration |
| `--max-iterations` | 1 | Iterations per issue (higher for complex work) |
| `--worktree` | off | Isolated git worktree per issue |
| `--verbose` | off | Stream agent actions to stderr |
| `--quality-check` | none | Shell command quality gate (repeatable) |
| `--max-quality-retries` | 2 | Retry limit for quality fix attempts |

## License

MIT
