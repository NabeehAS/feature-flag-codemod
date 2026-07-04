from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from project.validation.runner import ValidationResult

try:
    import docker
except ImportError:  # pragma: no cover - covered by runtime behavior
    docker = None


class DockerSandboxRunner:
    """
    Runs pytest inside a restricted Docker container.

    Security posture:
    - Repository is mounted read-only.
    - Container network is disabled.
    - Linux capabilities are dropped.
    - Root filesystem is read-only.
    - /tmp is provided as a small tmpfs for pytest/temp files.
    - Container is removed after execution.
    """

    def __init__(
        self,
        repo_root: Path,
        image: str = "feature-flag-undertaker-sandbox:py312",
        command: Optional[List[str]] = None,
        timeout_seconds: int = 300,
        memory_limit: str = "512m",
        client: Optional[Any] = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.image = image
        self.command = command or [
            "python",
            "-m",
            "pytest",
            ".",
            "-q",
            "--disable-warnings",
            "-p",
            "no:cacheprovider",
        ]
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.client = client

    def run_tests(self) -> ValidationResult:
        if not self.repo_root.is_dir():
            return ValidationResult(
                passed=False,
                output=f"Sandbox validation failed: {self.repo_root} is not a directory.",
                returncode=None,
            )

        try:
            client = self._get_client()
        except Exception as e:
            return ValidationResult(
                passed=False,
                output=f"Docker is unavailable: {str(e)}",
                returncode=None,
            )

        container = None

        try:
            container = client.containers.run(
                image=self.image,
                command=self.command,
                detach=True,
                remove=False,
                working_dir="/workspace",
                volumes={
                    str(self.repo_root): {
                        "bind": "/workspace",
                        "mode": "ro",
                    }
                },
                network_mode="none",
                cap_drop=["ALL"],
                read_only=True,
                security_opt=["no-new-privileges"],
                mem_limit=self.memory_limit,
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                },
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=128m",
                },
            )

            wait_result = container.wait(timeout=self.timeout_seconds)
            returncode = int(wait_result.get("StatusCode", 1))

            output = self._decode_output(
                container.logs(stdout=True, stderr=True)
            )

            return ValidationResult(
                passed=returncode == 0,
                output=output,
                returncode=returncode,
                timed_out=False,
            )

        except Exception as e:
            output = self._safe_container_logs(container)

            if not output:
                output = self._extract_exception_output(e)

            timed_out = self._looks_like_timeout(e)

            if timed_out:
                output = (
                    output
                    or f"Sandbox validation timed out after {self.timeout_seconds} seconds."
                )

            if container is not None:
                self._safe_stop(container)

            return ValidationResult(
                passed=False,
                output=output,
                returncode=None,
                timed_out=timed_out,
            )

        finally:
            if container is not None:
                self._safe_remove(container)

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client

        if docker is None:
            raise RuntimeError(
                "The Docker SDK for Python is not installed. "
                "Run: pip install docker"
            )

        return docker.from_env()

    def _safe_container_logs(self, container: Optional[Any]) -> str:
        if container is None:
            return ""

        try:
            return self._decode_output(container.logs(stdout=True, stderr=True))
        except Exception:
            return ""

    def _safe_stop(self, container: Any) -> None:
        try:
            container.stop(timeout=1)
        except Exception:
            pass

    def _safe_remove(self, container: Any) -> None:
        try:
            container.remove(force=True)
        except Exception:
            pass

    def _decode_output(self, output: Any) -> str:
        if output is None:
            return ""

        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")

        return str(output)

    def _extract_exception_output(self, error: Exception) -> str:
        stderr = getattr(error, "stderr", None)

        if stderr:
            return self._decode_output(stderr)

        explanation = getattr(error, "explanation", None)

        if explanation:
            return self._decode_output(explanation)

        return str(error)

    def _looks_like_timeout(self, error: Exception) -> bool:
        error_name = error.__class__.__name__.lower()
        error_message = str(error).lower()

        return "timeout" in error_name or "timed out" in error_message