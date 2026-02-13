"""Tests for DAG dependency parsing, topological sort, and wave computation."""

import pytest

from autoclaude.dag import CycleError, build_dag, parse_deps, topological_waves
from autoclaude.models import DAGNode, DAGResult, DAGWave, MergeConflict, ProcessingResult, ProcessingStatus


# --- parse_deps ---


def test_parse_deps_simple():
    deps = parse_deps("197:200,198:197")
    assert deps == {197: [200], 198: [197]}


def test_parse_deps_empty():
    assert parse_deps("") == {}
    assert parse_deps("  ") == {}


def test_parse_deps_no_colon_ignored():
    assert parse_deps("197") == {}


def test_parse_deps_multiple_deps_for_one_ticket():
    deps = parse_deps("10:20,10:30")
    assert deps == {10: [20, 30]}


def test_parse_deps_whitespace_tolerant():
    deps = parse_deps(" 1 : 2 , 3 : 4 ")
    assert deps == {1: [2], 3: [4]}


# --- build_dag ---


def test_build_dag_basic():
    nodes = build_dag([1, 2, 3], {1: [2]})
    assert len(nodes) == 3
    assert nodes[0].ticket_number == 1
    assert nodes[0].depends_on == [2]
    assert nodes[1].depends_on == []


def test_build_dag_validates_unknown_dep():
    with pytest.raises(ValueError, match="not in the ticket list"):
        build_dag([1, 2], {1: [999]})


def test_build_dag_no_deps():
    nodes = build_dag([10, 20, 30], {})
    assert all(n.depends_on == [] for n in nodes)


# --- topological_waves ---


def test_topological_waves_all_independent():
    nodes = [DAGNode(1), DAGNode(2), DAGNode(3)]
    waves = topological_waves(nodes)
    assert len(waves) == 1
    assert sorted(waves[0].tickets) == [1, 2, 3]


def test_topological_waves_linear_chain():
    nodes = [
        DAGNode(1, depends_on=[2]),
        DAGNode(2, depends_on=[3]),
        DAGNode(3),
    ]
    waves = topological_waves(nodes)
    assert len(waves) == 3
    assert waves[0].tickets == [3]
    assert waves[1].tickets == [2]
    assert waves[2].tickets == [1]


def test_topological_waves_diamond():
    nodes = [
        DAGNode(1),
        DAGNode(2, depends_on=[1]),
        DAGNode(3, depends_on=[1]),
        DAGNode(4, depends_on=[2, 3]),
    ]
    waves = topological_waves(nodes)
    assert len(waves) == 3
    assert waves[0].tickets == [1]
    assert sorted(waves[1].tickets) == [2, 3]
    assert waves[2].tickets == [4]


def test_topological_waves_spec_example():
    """Test the exact example from the feature request."""
    tickets = [197, 198, 199, 200, 201, 202]
    deps = parse_deps("197:200,198:197")
    nodes = build_dag(tickets, deps)
    waves = topological_waves(nodes)

    assert len(waves) == 3
    assert sorted(waves[0].tickets) == [199, 200, 201, 202]
    assert waves[1].tickets == [197]
    assert waves[2].tickets == [198]


def test_topological_waves_cycle_raises():
    nodes = [
        DAGNode(1, depends_on=[2]),
        DAGNode(2, depends_on=[1]),
    ]
    with pytest.raises(CycleError, match="cycle"):
        topological_waves(nodes)


def test_topological_waves_self_cycle():
    nodes = [DAGNode(1, depends_on=[1])]
    with pytest.raises(CycleError):
        topological_waves(nodes)


def test_topological_waves_assigns_wave_numbers():
    nodes = [DAGNode(1), DAGNode(2, depends_on=[1])]
    waves = topological_waves(nodes)
    # After sorting, node wave numbers should be set
    node_map = {n.ticket_number: n for n in nodes}
    assert node_map[1].wave == 1
    assert node_map[2].wave == 2


def test_topological_waves_single_ticket():
    nodes = [DAGNode(42)]
    waves = topological_waves(nodes)
    assert len(waves) == 1
    assert waves[0].tickets == [42]


# --- DAGResult ---


def test_dag_result_summary():
    result = DAGResult(
        waves=[DAGWave(1, [10, 11]), DAGWave(2, [12])],
        results_by_ticket={
            10: ProcessingResult(10, ProcessingStatus.COMPLETED, branch_name="issue-10-fix"),
            11: ProcessingResult(11, ProcessingStatus.FAILED, error_message="merge failed"),
            12: ProcessingResult(12, ProcessingStatus.COMPLETED, branch_name="issue-12-feat"),
        },
    )
    summary = result.summary()
    assert "Wave 1" in summary
    assert "Wave 2" in summary
    assert "#10" in summary
    assert "Completed: 2" in summary
    assert "Failed: 1" in summary


def test_dag_result_summary_with_conflicts():
    result = DAGResult(
        waves=[DAGWave(1, [10, 11])],
        merge_conflicts=[
            MergeConflict(ticket_a=10, ticket_b=11, overlapping_files=["src/main.py"]),
        ],
    )
    summary = result.summary()
    assert "overlap" in summary.lower()
    assert "src/main.py" in summary


def test_dag_result_summary_with_test():
    result = DAGResult(waves=[], test_passed=True)
    assert "PASSED" in result.summary()

    result2 = DAGResult(waves=[], test_passed=False)
    assert "FAILED" in result2.summary()


# --- MergeConflict model ---


def test_merge_conflict_model():
    mc = MergeConflict(ticket_a=10, ticket_b=11, overlapping_files=["a.py", "b.py"])
    assert mc.ticket_a == 10
    assert mc.ticket_b == 11
    assert len(mc.overlapping_files) == 2
