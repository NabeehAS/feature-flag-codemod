import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class ValidationResult:
    """Result returned by a validation test run."""

    passed: bool
    output: str
    returncode: Optional[int] = None
    timed_out: bool = False


class LocalTestRunner:
    """
    Runs a repository's pytest suite locally.

    V4 intentionally runs locally.
    V5 will replace or wrap this with Docker sandbox execution.
    """

    def __init__(
        self,
        repo_root: Path,
        command: Optional[List[str]] = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.command = command or [
            sys.executable,
            "-m",
            "pytest",
            ".",
            "-q",
            "--disable-warnings",
        ]
        self.timeout_seconds = timeout_seconds

    def run(self) -> ValidationResult:
        if not self.repo_root.is_dir():
            return ValidationResult(
                passed=False,
                output=f"Validation failed: {self.repo_root} is not a directory.",
                returncode=None,
            )

        try:
            result = subprocess.run(
                self.command,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )

            output = self._combine_output(result.stdout, result.stderr)

            return ValidationResult(
                passed=result.returncode == 0,
                output=output,
                returncode=result.returncode,
            )

        except subprocess.TimeoutExpired as e:
            output = self._combine_output(e.stdout, e.stderr)

            if not output:
                output = (
                    f"Validation timed out after "
                    f"{self.timeout_seconds} seconds."
                )

            return ValidationResult(
                passed=False,
                output=output,
                returncode=None,
                timed_out=True,
            )

        except Exception as e:
            return ValidationResult(
                passed=False,
                output=f"Validation execution error: {str(e)}",
                returncode=None,
            )

    def _combine_output(
        self,
        stdout: Optional[str],
        stderr: Optional[str],
    ) -> str:
        parts = []

        if stdout:
            parts.append(stdout.strip())

        if stderr:
            parts.append(stderr.strip())

        return "\n\n".join(parts)