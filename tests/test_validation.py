import sys
from pathlib import Path

from project.validation.runner import LocalTestRunner


def test_local_runner_passes_when_pytest_passes(tmp_path):
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "def test_passes():\n"
        "    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    runner = LocalTestRunner(repo_root=tmp_path)
    result = runner.run()

    assert result.passed is True
    assert result.returncode == 0
    assert result.timed_out is False


def test_local_runner_fails_when_pytest_fails(tmp_path):
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "def test_fails():\n"
        "    assert False\n",
        encoding="utf-8",
    )

    runner = LocalTestRunner(repo_root=tmp_path)
    result = runner.run()

    assert result.passed is False
    assert result.returncode != 0
    assert result.timed_out is False
    assert "assert False" in result.output or "FAILED" in result.output


def test_local_runner_rejects_invalid_repo_path(tmp_path):
    missing_dir = tmp_path / "does_not_exist"

    runner = LocalTestRunner(repo_root=missing_dir)
    result = runner.run()

    assert result.passed is False
    assert result.returncode is None
    assert "not a directory" in result.output


def test_local_runner_handles_timeout(tmp_path):
    runner = LocalTestRunner(
        repo_root=tmp_path,
        command=[
            sys.executable,
            "-c",
            "import time; time.sleep(2)",
        ],
        timeout_seconds=1,
    )

    result = runner.run()

    assert result.passed is False
    assert result.timed_out is True
    assert result.returncode is None