import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "project.cli.main", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_file_mode_mutates_and_cleans_imports(tmp_path):
    target_file = tmp_path / "app.py"

    target_file.write_text(
        "from service import old_func\n"
        "if OLD_FLAG:\n"
        "    old_func()\n"
        "else:\n"
        "    keep()\n",
        encoding="utf-8",
    )

    result = run_cli(
        "--file",
        str(target_file),
        "--flag",
        "OLD_FLAG",
        "--state",
        "false",
    )

    assert result.returncode == 0, result.stderr
    assert target_file.read_text(encoding="utf-8") == "keep()\n"


def test_cli_does_not_cleanup_file_when_flag_is_not_found(tmp_path):
    target_file = tmp_path / "app.py"

    original_source = (
        "from service import unused_func\n"
        "keep()\n"
    )

    target_file.write_text(original_source, encoding="utf-8")

    result = run_cli(
        "--file",
        str(target_file),
        "--flag",
        "MISSING_FLAG",
        "--state",
        "false",
    )

    assert result.returncode == 0, result.stderr
    assert target_file.read_text(encoding="utf-8") == original_source


def test_cli_directory_mode_mutates_and_cleans_multiple_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    first_file = repo / "first.py"
    second_file = repo / "second.py"

    first_file.write_text(
        "from service import old_func\n"
        "if OLD_FLAG:\n"
        "    old_func()\n"
        "else:\n"
        "    keep_first()\n",
        encoding="utf-8",
    )

    second_file.write_text(
        "from service import old_func, keep_func\n"
        "if OLD_FLAG:\n"
        "    old_func()\n"
        "keep_func()\n",
        encoding="utf-8",
    )

    result = run_cli(
        "--dir",
        str(repo),
        "--flag",
        "OLD_FLAG",
        "--state",
        "false",
    )

    assert result.returncode == 0, result.stderr
    assert first_file.read_text(encoding="utf-8") == "keep_first()\n"

    assert second_file.read_text(encoding="utf-8") == (
        "from service import keep_func\n"
        "keep_func()\n"
    )


def test_cli_rejects_non_python_file(tmp_path):
    target_file = tmp_path / "notes.txt"
    target_file.write_text("if OLD_FLAG:\n    run()\n", encoding="utf-8")

    result = run_cli(
        "--file",
        str(target_file),
        "--flag",
        "OLD_FLAG",
        "--state",
        "true",
    )

    assert result.returncode != 0
    assert "not a Python file" in result.stderr