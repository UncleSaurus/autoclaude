"""Tests for quality gate hooks."""

import os
import tempfile

from autoclaude.quality import QualityCheck, QualityResult, discover_checks, run_checks


def test_discover_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        checks = discover_checks(tmp)
        assert checks == []


def test_discover_empty_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        ac_dir = os.path.join(tmp, ".autoclaude")
        os.makedirs(ac_dir)
        with open(os.path.join(ac_dir, "quality.yaml"), "w") as f:
            f.write("")
        checks = discover_checks(tmp)
        assert checks == []


def test_discover_string_commands():
    with tempfile.TemporaryDirectory() as tmp:
        ac_dir = os.path.join(tmp, ".autoclaude")
        os.makedirs(ac_dir)
        with open(os.path.join(ac_dir, "quality.yaml"), "w") as f:
            f.write("checks:\n  - pytest\n  - ruff check .\n")
        checks = discover_checks(tmp)
        assert len(checks) == 2
        assert checks[0].name == "pytest"
        assert checks[0].command == "pytest"
        assert checks[1].name == "ruff check ."
        assert checks[1].command == "ruff check ."


def test_discover_dict_commands():
    with tempfile.TemporaryDirectory() as tmp:
        ac_dir = os.path.join(tmp, ".autoclaude")
        os.makedirs(ac_dir)
        with open(os.path.join(ac_dir, "quality.yaml"), "w") as f:
            f.write("checks:\n  - name: Unit Tests\n    command: pytest --unit\n")
        checks = discover_checks(tmp)
        assert len(checks) == 1
        assert checks[0].name == "Unit Tests"
        assert checks[0].command == "pytest --unit"


def test_run_checks_empty():
    result = run_checks([])
    assert result.passed is True
    assert result.results == []


def test_run_checks_passing():
    checks = [QualityCheck(name="true", command="true")]
    result = run_checks(checks)
    assert result.passed is True
    assert len(result.results) == 1
    assert result.results[0]["passed"] is True


def test_run_checks_failing():
    checks = [QualityCheck(name="false", command="false")]
    result = run_checks(checks)
    assert result.passed is False
    assert len(result.results) == 1
    assert result.results[0]["passed"] is False


def test_run_checks_mixed():
    checks = [
        QualityCheck(name="pass", command="true"),
        QualityCheck(name="fail", command="false"),
    ]
    result = run_checks(checks)
    assert result.passed is False
    assert result.results[0]["passed"] is True
    assert result.results[1]["passed"] is False


def test_run_checks_dry_run():
    checks = [QualityCheck(name="dangerous", command="rm -rf /")]
    result = run_checks(checks, dry_run=True)
    assert result.passed is True
    assert result.results[0]["passed"] is True


def test_failure_summary():
    result = QualityResult(passed=False, results=[
        {"name": "tests", "passed": False, "exit_code": 1, "output": "FAILED test_foo"},
        {"name": "lint", "passed": True, "exit_code": 0, "output": ""},
    ])
    summary = result.failure_summary()
    assert "tests" in summary
    assert "FAILED test_foo" in summary
    assert "lint" not in summary


def test_failure_summary_truncation():
    long_output = "x" * 5000
    result = QualityResult(passed=False, results=[
        {"name": "long", "passed": False, "exit_code": 1, "output": long_output},
    ])
    summary = result.failure_summary()
    assert "... (truncated)" in summary
    assert len(summary) < 5000


def test_failure_summary_all_passing():
    result = QualityResult(passed=True, results=[
        {"name": "tests", "passed": True, "exit_code": 0, "output": ""},
    ])
    assert result.failure_summary() == ""
