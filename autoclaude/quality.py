"""Quality gate hooks for AutoClaude.

Runs project-defined quality checks (tests, linters, etc.) after the agent
completes implementation. Failed checks are fed back to the agent for fixing.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class QualityCheck:
    """A single quality check command."""

    name: str
    command: str


@dataclass
class QualityResult:
    """Result of running all quality checks."""

    passed: bool
    results: list[dict] = field(default_factory=list)

    def failure_summary(self) -> str:
        """Format failed checks for feeding back to the agent."""
        failures = [r for r in self.results if not r["passed"]]
        if not failures:
            return ""

        sections = []
        for f in failures:
            output = f["output"]
            # Truncate very long output to avoid overwhelming the agent
            if len(output) > 3000:
                output = output[:3000] + "\n... (truncated)"
            sections.append(f"### {f['name']} (exit code {f['exit_code']})\n```\n{output}\n```")

        return "\n\n".join(sections)


def discover_checks(root: str | Path) -> list[QualityCheck]:
    """Discover quality checks from .autoclaude/quality.yaml in the project."""
    quality_path = Path(root) / ".autoclaude" / "quality.yaml"
    if not quality_path.exists():
        return []

    with open(quality_path) as f:
        data = yaml.safe_load(f)

    if not data or "checks" not in data:
        return []

    checks = []
    for entry in data["checks"]:
        if isinstance(entry, str):
            checks.append(QualityCheck(name=entry, command=entry))
        elif isinstance(entry, dict):
            checks.append(QualityCheck(
                name=entry.get("name", entry.get("command", "unnamed")),
                command=entry["command"],
            ))

    return checks


def run_checks(
    checks: list[QualityCheck],
    cwd: Optional[str] = None,
    dry_run: bool = False,
) -> QualityResult:
    """Run all quality checks and return results.

    Args:
        checks: List of quality checks to run.
        cwd: Working directory for commands.
        dry_run: If True, print commands without running.
    """
    if not checks:
        return QualityResult(passed=True)

    results = []
    all_passed = True

    for check in checks:
        if dry_run:
            print(f"  [DRY RUN] Would run quality check: {check.name}: {check.command}")
            results.append({"name": check.name, "passed": True, "exit_code": 0, "output": ""})
            continue

        print(f"  Running quality check: {check.name}...")

        try:
            proc = subprocess.run(
                check.command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=300,
            )

            passed = proc.returncode == 0
            output = proc.stdout + proc.stderr

            if passed:
                print(f"    PASS: {check.name}")
            else:
                print(f"    FAIL: {check.name} (exit code {proc.returncode})")
                all_passed = False

            results.append({
                "name": check.name,
                "passed": passed,
                "exit_code": proc.returncode,
                "output": output.strip(),
            })

        except subprocess.TimeoutExpired:
            print(f"    TIMEOUT: {check.name} (>300s)")
            all_passed = False
            results.append({
                "name": check.name,
                "passed": False,
                "exit_code": -1,
                "output": "Command timed out after 300 seconds",
            })

        except Exception as e:
            print(f"    ERROR: {check.name}: {e}")
            all_passed = False
            results.append({
                "name": check.name,
                "passed": False,
                "exit_code": -1,
                "output": str(e),
            })

    return QualityResult(passed=all_passed, results=results)
