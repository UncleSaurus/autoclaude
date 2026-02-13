"""DAG-based dependency-aware batch processing with merge queue.

Parses ticket dependencies into a directed acyclic graph, topologically sorts
into waves of independent tickets, processes each wave in parallel using git
worktrees, and merges completed branches into main between waves.
"""

import asyncio
import copy
import subprocess
from collections import defaultdict
from datetime import datetime

from .config import AutoClaudeConfig
from .models import (
    DAGNode,
    DAGResult,
    DAGWave,
    MergeConflict,
    ProcessingResult,
    ProcessingStatus,
)
from .processor import TicketProcessor


class CycleError(ValueError):
    """Raised when the dependency graph contains a cycle."""

    pass


# --- Pure functions ---


def parse_deps(dep_spec: str) -> dict[int, list[int]]:
    """Parse dependency spec string into {ticket: [depends_on]} dict.

    Format: "197:200,198:197" means 197 depends on 200, 198 depends on 197.
    """
    deps: dict[int, list[int]] = defaultdict(list)
    if not dep_spec or not dep_spec.strip():
        return dict(deps)
    for pair in dep_spec.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        child_str, parent_str = pair.split(":", 1)
        child = int(child_str.strip())
        parent = int(parent_str.strip())
        deps[child].append(parent)
    return dict(deps)


def build_dag(tickets: list[int], deps: dict[int, list[int]]) -> list[DAGNode]:
    """Build DAG nodes from ticket list and dependency map.

    Validates that all dependencies reference known tickets.
    """
    all_tickets = set(tickets)
    nodes = []
    for t in tickets:
        node_deps = deps.get(t, [])
        for dep in node_deps:
            if dep not in all_tickets:
                raise ValueError(
                    f"Ticket #{t} depends on #{dep}, which is not in the ticket list"
                )
        nodes.append(DAGNode(ticket_number=t, depends_on=node_deps))
    return nodes


def topological_waves(nodes: list[DAGNode]) -> list[DAGWave]:
    """Topologically sort DAG into waves using Kahn's algorithm.

    Independent tickets (no unmet dependencies) go in the same wave.
    Returns waves in execution order.

    Raises CycleError if the graph contains a cycle.
    """
    in_degree: dict[int, int] = {}
    dependents: dict[int, list[int]] = defaultdict(list)  # parent -> children
    node_map: dict[int, DAGNode] = {}

    for node in nodes:
        node_map[node.ticket_number] = node
        in_degree[node.ticket_number] = len(node.depends_on)
        for dep in node.depends_on:
            dependents[dep].append(node.ticket_number)

    waves = []
    remaining = set(in_degree.keys())
    wave_num = 1

    while remaining:
        ready = [t for t in remaining if in_degree[t] == 0]
        if not ready:
            raise CycleError(
                f"Dependency cycle detected among tickets: {sorted(remaining)}"
            )

        wave = DAGWave(wave_number=wave_num, tickets=sorted(ready))
        waves.append(wave)

        for t in ready:
            remaining.remove(t)
            node_map[t].wave = wave_num
            for child in dependents.get(t, []):
                in_degree[child] -= 1

        wave_num += 1

    return waves


# --- MergeQueue ---


class MergeQueue:
    """Handles merging completed branches into main between waves.

    Operates on the MAIN repo directory (not worktrees).
    """

    def __init__(self, config: AutoClaudeConfig):
        self.config = config

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run git in the main repo directory."""
        cmd = ["git"] + list(args)
        cwd = self.config.repo_dir or None
        if self.config.dry_run:
            print(f"[DRY RUN] Would run: {' '.join(cmd)}", flush=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)

    def detect_file_overlaps(self, branches: dict[int, str]) -> list[MergeConflict]:
        """Detect files modified by multiple branches in the same wave.

        Args:
            branches: {ticket_number: branch_name} for completed tickets in a wave.

        Returns:
            List of MergeConflict for overlapping file modifications.
        """
        if self.config.dry_run:
            return []

        base = f"{self.config.git_remote}/{self.config.base_branch}"
        files_by_ticket: dict[int, set[str]] = {}

        for ticket, branch in branches.items():
            result = self._run_git("diff", "--name-only", f"{base}...{branch}", check=False)
            if result.returncode == 0:
                files = {f.strip() for f in result.stdout.strip().splitlines() if f.strip()}
                files_by_ticket[ticket] = files

        conflicts = []
        ticket_list = sorted(files_by_ticket.keys())
        for i, t_a in enumerate(ticket_list):
            for t_b in ticket_list[i + 1 :]:
                overlap = files_by_ticket.get(t_a, set()) & files_by_ticket.get(t_b, set())
                if overlap:
                    conflicts.append(
                        MergeConflict(
                            ticket_a=t_a,
                            ticket_b=t_b,
                            overlapping_files=sorted(overlap),
                        )
                    )

        return conflicts

    def merge_branches(self, branches: dict[int, str]) -> list[int]:
        """Merge completed branches into main, one at a time.

        Args:
            branches: {ticket_number: branch_name} for completed tickets.

        Returns:
            List of ticket numbers that merged successfully.
        """
        remote = self.config.git_remote
        base = self.config.base_branch

        self._run_git("checkout", base)
        self._run_git("pull", remote, base)

        merged = []
        for ticket in sorted(branches.keys()):
            branch = branches[ticket]
            print(f"  Merging #{ticket} ({branch}) into {base}...", flush=True)

            result = self._run_git(
                "merge",
                "--no-ff",
                "-m",
                f"Merge {branch} (#{ticket})",
                branch,
                check=False,
            )

            if result.returncode != 0:
                print(f"    MERGE FAILED for #{ticket}: {result.stderr}", flush=True)
                self._run_git("merge", "--abort", check=False)
                continue

            merged.append(ticket)

        if merged and not self.config.dry_run:
            print(f"  Pushing {base} with {len(merged)} merged branches...", flush=True)
            self._run_git("push", remote, base)

        return merged

    def refresh_remote(self) -> None:
        """Fetch latest from remote so next wave's worktrees are up-to-date."""
        self._run_git("fetch", self.config.git_remote)

    def run_test_command(self, command: str) -> bool:
        """Run post-merge validation command. Returns True if passed."""
        if self.config.dry_run:
            print(f"[DRY RUN] Would run test: {command}", flush=True)
            return True

        print(f"  Running post-merge test: {command}", flush=True)
        cwd = self.config.repo_dir or None
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=600,
        )
        if result.returncode == 0:
            print("    TEST PASSED", flush=True)
            return True
        else:
            print(f"    TEST FAILED (exit {result.returncode})", flush=True)
            if result.stdout:
                print(f"    {result.stdout[-500:]}", flush=True)
            return False


# --- DAGProcessor ---


class DAGProcessor:
    """Dependency-aware batch processor with merge queue."""

    def __init__(self, config: AutoClaudeConfig):
        self.config = config
        self.merge_queue = MergeQueue(config)
        self._deps_for: dict[int, list[int]] = {}

    def _make_ticket_config(self, ticket_number: int) -> AutoClaudeConfig:
        """Create an isolated config for a single ticket.

        Each parallel ticket gets its own config copy with use_worktree=True.
        """
        ticket_config = copy.copy(self.config)
        ticket_config.use_worktree = True
        ticket_config.worktree_path = None  # Set at runtime by create_worktree
        ticket_config.issue_number = ticket_number
        return ticket_config

    async def _process_ticket(self, ticket_number: int) -> ProcessingResult:
        """Process a single ticket in its own worktree."""
        ticket_config = self._make_ticket_config(ticket_number)
        processor = TicketProcessor(ticket_config)

        print(f"  Starting ticket #{ticket_number}...", flush=True)
        try:
            result = await processor.process_single(ticket_number)
            print(f"  Ticket #{ticket_number}: {result.status.value}", flush=True)
            return result
        except Exception as e:
            print(f"  Ticket #{ticket_number} EXCEPTION: {e}", flush=True)
            return ProcessingResult(
                issue_number=ticket_number,
                status=ProcessingStatus.FAILED,
                error_message=str(e),
                started_at=datetime.now(),
                completed_at=datetime.now(),
            )

    async def _process_wave(
        self, wave: DAGWave, failed_tickets: set[int]
    ) -> dict[int, ProcessingResult]:
        """Process all tickets in a wave concurrently.

        Tickets whose dependencies failed are automatically skipped.
        Concurrency is capped at config.max_parallel.
        """
        results: dict[int, ProcessingResult] = {}
        semaphore = asyncio.Semaphore(self.config.max_parallel)

        async def _limited_process(ticket: int) -> tuple[int, ProcessingResult]:
            async with semaphore:
                return (ticket, await self._process_ticket(ticket))

        # Filter out tickets whose deps failed
        runnable = []
        for ticket in wave.tickets:
            node_deps = self._deps_for.get(ticket, [])
            if any(d in failed_tickets for d in node_deps):
                print(f"  Skipping #{ticket}: dependency failed", flush=True)
                results[ticket] = ProcessingResult(
                    issue_number=ticket,
                    status=ProcessingStatus.FAILED,
                    error_message="Skipped: upstream dependency failed",
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                )
                continue
            runnable.append(ticket)

        if not runnable:
            return results

        tasks = [_limited_process(t) for t in runnable]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, Exception):
                continue
            ticket, result = item
            results[ticket] = result

        return results

    async def run(
        self,
        tickets: list[int],
        deps: dict[int, list[int]],
    ) -> DAGResult:
        """Execute the full DAG processing pipeline.

        1. Build DAG and compute waves
        2. For each wave:
           a. Process tickets in parallel (worktrees + asyncio)
           b. Detect file overlaps between completed branches
           c. Merge completed branches into main
           d. Refresh remote for next wave
        3. Optionally run post-merge test command
        """
        dag_result = DAGResult(waves=[], started_at=datetime.now())

        # Build DAG
        nodes = build_dag(tickets, deps)
        waves = topological_waves(nodes)
        dag_result.waves = waves

        # Build dependency lookup for skip logic
        self._deps_for = {n.ticket_number: n.depends_on for n in nodes}

        # Print wave plan
        print(f"\nDAG Plan: {len(waves)} waves, {len(tickets)} tickets", flush=True)
        for wave in waves:
            print(
                f"  Wave {wave.wave_number}: {', '.join(f'#{t}' for t in wave.tickets)}",
                flush=True,
            )
        print(flush=True)

        failed_tickets: set[int] = set()

        for wave in waves:
            print(f"\n{'=' * 50}", flush=True)
            print(f"WAVE {wave.wave_number}", flush=True)
            print(f"{'=' * 50}", flush=True)

            # Process wave
            wave_results = await self._process_wave(wave, failed_tickets)
            dag_result.results_by_ticket.update(wave_results)

            # Collect completed branches for merge
            completed_branches: dict[int, str] = {}
            for ticket, result in wave_results.items():
                if result.status == ProcessingStatus.COMPLETED and result.branch_name:
                    completed_branches[ticket] = result.branch_name
                elif result.status == ProcessingStatus.FAILED:
                    failed_tickets.add(ticket)

            if not completed_branches:
                print(f"  No branches to merge in wave {wave.wave_number}", flush=True)
                continue

            # Detect file overlaps
            if len(completed_branches) > 1:
                conflicts = self.merge_queue.detect_file_overlaps(completed_branches)
                dag_result.merge_conflicts.extend(conflicts)
                for mc in conflicts:
                    print(
                        f"  WARNING: File overlap between #{mc.ticket_a} and #{mc.ticket_b}: "
                        f"{', '.join(mc.overlapping_files[:3])}",
                        flush=True,
                    )

            # Merge into main
            merged = self.merge_queue.merge_branches(completed_branches)
            for ticket in completed_branches:
                if ticket not in merged:
                    failed_tickets.add(ticket)
                    dag_result.results_by_ticket[ticket].status = ProcessingStatus.FAILED
                    dag_result.results_by_ticket[ticket].error_message = (
                        "Merge into main failed"
                    )

            # Refresh remote for next wave
            if merged:
                self.merge_queue.refresh_remote()

        # Post-merge test
        if self.config.test_command:
            self.merge_queue._run_git("checkout", self.config.base_branch)
            dag_result.test_passed = self.merge_queue.run_test_command(
                self.config.test_command
            )

        dag_result.completed_at = datetime.now()
        return dag_result
