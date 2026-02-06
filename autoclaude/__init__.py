"""AutoClaude - Autonomous GitHub issue processor powered by Claude."""

from .config import AutoClaudeConfig
from .context import discover_context_files, load_context
from .loop import IterationLoop
from .models import IssueContext, ProcessingResult, ProcessingStatus
from .orchestrator import Orchestrator, OrchestrationResult, RepoConfig, RepoRelationship, create_multi_repo_orchestrator
from .processor import TicketProcessor
from .progress import append_run, extract_learnings

__all__ = [
    "AutoClaudeConfig",
    "IterationLoop",
    "IssueContext",
    "Orchestrator",
    "OrchestrationResult",
    "ProcessingResult",
    "ProcessingStatus",
    "RepoConfig",
    "RepoRelationship",
    "TicketProcessor",
    "append_run",
    "create_multi_repo_orchestrator",
    "discover_context_files",
    "extract_learnings",
    "load_context",
]
