# AutoClaude

Autonomous GitHub and Azure DevOps issue processor powered by Claude. Picks up issues, implements changes, runs quality checks, and opens PRs — all without human intervention.

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
# GitHub (required for --platform github)
GITHUB_TOKEN=ghp_...           # GitHub PAT with repo access
GITHUB_BOT_ASSIGNEE=claude-bot # Bot username for claiming (optional)
GITHUB_HUMAN_REVIEWER=username # Human fallback for blocked issues (optional)

# Azure DevOps (required for --platform azuredevops)
ADO_ORG=MyOrg                  # ADO organization name
ADO_PROJECT=MyProject          # ADO project name
ADO_REPO=my-repo               # ADO repository name

# Claude auth (optional — defaults to OAuth via `claude login`)
ANTHROPIC_API_KEY=sk-ant-...   # Only needed with --use-api-key
```

Variables can be set in a `.env` file in the project root (loaded automatically via dotenv). The git repo root `.env` is also checked, so worktree invocations inherit the main project's env.

By default, AutoClaude uses OAuth authentication via `claude login` (Max plan tokens). Pass `--use-api-key` to use `ANTHROPIC_API_KEY` for billing instead.

## Quick Start

### GitHub

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

### Azure DevOps

```bash
# Process a specific work item
autoclaude claim --platform azuredevops --ado-org MapLarge --ado-project "Data Science" --ado-repo my-repo --issue 183

# Or use environment variables (ADO_ORG, ADO_PROJECT, ADO_REPO)
autoclaude claim --platform azuredevops --issue 183

# Process all claimable work items with a tag
autoclaude claim --platform azuredevops --label enhancement
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

## DAG Mode (Dependency-Aware Batch)

Process multiple tickets with dependency ordering, parallel execution, and automatic merge queue:

```bash
autoclaude dag \
  --tickets 197,198,199,200,201,202 \
  --deps "197:200,198:197" \
  --repo owner/repo --max-parallel 3
```

This parses the dependency spec into a DAG and computes execution waves:
- **Wave 1:** 199, 200, 201, 202 (independent — run in parallel)
- **Wave 2:** 197 (depends on 200)
- **Wave 3:** 198 (depends on 197)

Each wave processes tickets in parallel using isolated git worktrees. After a wave completes, branches are merged into main and the remote is refreshed before the next wave starts.

| Flag | Default | Description |
|------|---------|-------------|
| `--tickets` | required | Comma-separated ticket numbers |
| `--deps` | none | Dependency spec: `"A:B,C:A"` means A depends on B, C on A |
| `--max-parallel` | 4 | Max concurrent tickets per wave |
| `--no-pr` | off | Skip PR creation (merge locally only) |
| `--test-command` | none | Shell command to run as post-merge validation |

File overlaps between parallel branches in the same wave are detected and reported as warnings before merging. If a ticket fails, all downstream dependents are automatically skipped.

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

## Permission Guard

AutoClaude uses Claude Agent SDK **PreToolUse hooks** instead of `bypassPermissions`. This means it works with both API key auth and OAuth/Max plan auth — no `ANTHROPIC_API_KEY` required.

The orchestrator acts as the security gate, auto-approving safe operations and blocking dangerous ones:

**Always allowed:** Read, Glob, Grep, WebSearch, WebFetch (read-only tools)

**Allowed with path validation:** Write, Edit (blocked for system paths, credentials, `.env` files)

**Bash — validated per-command.** Blocked patterns include:
- Destructive: `rm -rf`, `sudo rm`, `mkfs`, `dd of=/dev/`
- Git destruction: `push --force`, `reset --hard`, `clean -f`, `checkout .`
- System: `sudo`, `kill -9`, `shutdown`, `chmod 777`
- Exfiltration: `curl --data`, `printenv`
- Database: `DROP TABLE`, `DELETE FROM ... ;` (no WHERE)

Blocked commands are denied with a message — the agent retries with a safer alternative.

To customize, edit `autoclaude/permission_guard.py`.

## How It Works

1. **Claim** — Labels issue `agent-claimed`, creates a branch
2. **Analyze** — Optionally checks if issue needs clarification first
3. **Implement** — Spawns Claude Agent SDK session with permission-guarded tool access
4. **Quality gate** — Runs project-defined checks, feeds failures back for fixing
5. **Commit & push** — Commits changes (excluding `.autoclaude/`), pushes branch
6. **CI** — Waits for CI, attempts fixes if it fails (up to 3 retries)
7. **PR** — Opens pull request with descriptive summary
8. **Clean up** — Removes worktree, updates labels

## Best Practices

### Always use `--worktree` for batch processing

Without `--worktree`, autoclaude checks out branches directly in your working tree. This means:
- Your working directory gets clobbered between issues
- Parallel processing is impossible
- Uncommitted changes in your checkout will conflict or be lost

Use `--worktree` for all multi-issue runs. It creates isolated worktrees (e.g. `../your-repo-issue-42/`) and cleans them up after each issue.

### Write well-defined tickets for autonomous processing

The agent works best with explicit, unambiguous tickets. Include:
- **Exact files to modify** (paths, not descriptions)
- **Implementation notes** with specific approaches, not open-ended design questions
- **Test requirements** — what tests to add or update
- **Definition of Done** — concrete acceptance criteria

Use `--skip-clarification` for tickets that are already fully specified. This skips the analysis phase where the agent might ask questions nobody is around to answer, saving a full iteration.

### Run from the target repository directory

AutoClaude performs git operations in the current working directory. If you invoke it from a different repo (e.g. a monorepo parent or a dependency), branches and commits will be created in the wrong repository.

```bash
# CORRECT: run from the target repo
cd /path/to/my-app
autoclaude claim --repo owner/my-app --issue 42 --worktree

# WRONG: running from a different directory
cd /path/to/some-other-repo
autoclaude claim --repo owner/my-app --issue 42  # branches created in wrong repo!
```

If autoclaude is installed in a different virtualenv, use the full path to the binary:

```bash
cd /path/to/my-app
/path/to/other-venv/bin/autoclaude claim --repo owner/my-app --issue 42 --worktree
```

### Running from within Claude Code

AutoClaude can be invoked from inside a Claude Code session. The `CLAUDECODE` environment variable (set by Claude Code 2.1+) is automatically stripped from subprocess environments so the nested CLI doesn't refuse to start.

### Use `--max-iterations 2+` for complex issues

The default `--max-iterations 1` gives the agent a single shot. For non-trivial work (refactoring, multi-file changes, security fixes), use `--max-iterations 2` or higher so the agent can self-correct after quality gate failures or incomplete implementations.

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
autoclaude claim        # Claim and process issues (primary command)
autoclaude dag          # Dependency-aware batch with merge queue
autoclaude batch        # PRD batch processing
autoclaude orchestrate  # Multi-repo upstream/downstream
autoclaude multi        # Custom multi-repo orchestration
autoclaude process      # Process assigned issues (legacy)
```

Common flags (all commands):

| Flag | Default | Description |
|------|---------|-------------|
| `--platform` | `github` | Ticket platform: `github` or `azuredevops` |
| `--model` | `sonnet` | Claude model: opus, sonnet, haiku |
| `--dry-run` | off | Preview without making changes |
| `--max-turns` | 50 | Max agent turns per iteration |
| `--max-iterations` | 1 | Iterations per issue (higher for complex work) |
| `--worktree` | off | Isolated git worktree per issue (strongly recommended) |
| `--verbose` | off | Stream agent actions to stderr |
| `--quality-check` | none | Shell command quality gate (repeatable) |
| `--max-quality-retries` | 2 | Retry limit for quality fix attempts |
| `--no-pr` | off | Skip PR creation |
| `--remote` | `origin` | Git remote for fetch/push |
| `--base-branch` | `main` | Base branch name |
| `--use-api-key` | off | Use ANTHROPIC_API_KEY for billing (default: OAuth) |
| `--skip-clarification` | off | Skip issue analysis/clarification phase |
| `--cli-path` | auto-detect | Path to claude CLI binary |

ADO-specific flags:

| Flag | Env Var | Description |
|------|---------|-------------|
| `--ado-org` | `ADO_ORG` | Azure DevOps organization |
| `--ado-project` | `ADO_PROJECT` | Azure DevOps project |
| `--ado-repo` | `ADO_REPO` | Azure DevOps repository name |

## License

MIT
