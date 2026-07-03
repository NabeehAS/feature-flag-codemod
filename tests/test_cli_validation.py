import subprocess
import sys
from pathlib import Path
from textwrap import dedent


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "project.cli.main", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def create_repo_with_test(tmp_path: Path, expected_value: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    app_file = repo / "app.py"
    app_file.write_text(
        dedent(
            """
            def keep():
                return "old"

            if OLD_FLAG:
                def target():
                    return "new"
            else:
                def target():
                    return keep()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    tests_dir = repo / "tests"
    tests_dir.mkdir()

    test_file = tests_dir / "test_app.py"
    test_file.write_text(
        dedent(
            f"""
            from app import target

            def test_target():
                assert target() == "{expected_value}"
            """
        ).lstrip(),
        encoding="utf-8",
    )

    return repo


def test_cli_validate_keeps_changes_when_tests_pass(tmp_path):
    repo = create_repo_with_test(tmp_path, expected_value="old")
    app_file = repo / "app.py"

    result = run_cli(
        "--dir",
        str(repo),
        "--flag",
        "OLD_FLAG",
        "--state",
        "false",
        "--validate",
        "--repo-root",
        str(repo),
    )

    assert result.returncode == 0, result.stderr

    updated_source = app_file.read_text(encoding="utf-8")

    assert "OLD_FLAG" not in updated_source
    assert "return keep()" in updated_source
    assert "Validation passed." in result.stdout


def test_cli_validate_rolls_back_when_tests_fail(tmp_path):
    repo = create_repo_with_test(tmp_path, expected_value="old")
    app_file = repo / "app.py"
    original_source = app_file.read_text(encoding="utf-8")

    result = run_cli(
        "--dir",
        str(repo),
        "--flag",
        "OLD_FLAG",
        "--state",
        "true",
        "--validate",
        "--repo-root",
        str(repo),
    )

    assert result.returncode != 0

    rolled_back_source = app_file.read_text(encoding="utf-8")

    assert rolled_back_source == original_source
    assert "Validation failed." in result.stderr
    assert "Rolling back changed files" in result.stdout


def test_cli_validate_skips_pytest_when_no_files_mutated(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    app_file = repo / "app.py"
    app_file.write_text(
        "def target():\n"
        "    return 'safe'\n",
        encoding="utf-8",
    )

    tests_dir = repo / "tests"
    tests_dir.mkdir()

    # This test would fail if validation ran.
    failing_test = tests_dir / "test_failure.py"
    failing_test.write_text(
        "def test_failure():\n"
        "    assert False\n",
        encoding="utf-8",
    )

    original_source = app_file.read_text(encoding="utf-8")

    result = run_cli(
        "--dir",
        str(repo),
        "--flag",
        "MISSING_FLAG",
        "--state",
        "false",
        "--validate",
        "--repo-root",
        str(repo),
    )

    assert result.returncode == 0, result.stderr
    assert app_file.read_text(encoding="utf-8") == original_source
    assert "No files were mutated. Skipping validation." in result.stdout


def test_cli_file_mode_validate_uses_explicit_repo_root(tmp_path):
    repo = create_repo_with_test(tmp_path, expected_value="old")
    app_file = repo / "app.py"

    result = run_cli(
        "--file",
        str(app_file),
        "--flag",
        "OLD_FLAG",
        "--state",
        "false",
        "--validate",
        "--repo-root",
        str(repo),
    )

    assert result.returncode == 0, result.stderr

    updated_source = app_file.read_text(encoding="utf-8")

    assert "OLD_FLAG" not in updated_source
    assert "Validation passed." in result.stdout