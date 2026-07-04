from pathlib import Path

from project.validation.sandbox import DockerSandboxRunner


class FakeContainer:
    def __init__(self, status_code=0, logs=b"ok") -> None:
        self.status_code = status_code
        self._logs = logs
        self.stopped = False
        self.removed = False

    def wait(self, timeout=None):
        return {"StatusCode": self.status_code}

    def logs(self, stdout=True, stderr=True):
        return self._logs

    def stop(self, timeout=1):
        self.stopped = True

    def remove(self, force=True):
        self.removed = True


class TimeoutContainer(FakeContainer):
    def wait(self, timeout=None):
        raise TimeoutError("container timed out")


class FakeContainers:
    def __init__(self, container) -> None:
        self.container = container
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self, container) -> None:
        self.containers = FakeContainers(container)


def test_docker_sandbox_passes_and_sets_security_restrictions(tmp_path):
    container = FakeContainer(status_code=0, logs=b"tests passed")
    client = FakeDockerClient(container)

    runner = DockerSandboxRunner(
        repo_root=tmp_path,
        image="test-sandbox-image",
        client=client,
    )

    result = runner.run_tests()

    assert result.passed is True
    assert result.returncode == 0
    assert "tests passed" in result.output

    kwargs = client.containers.kwargs

    assert kwargs["image"] == "test-sandbox-image"
    assert kwargs["working_dir"] == "/workspace"
    assert kwargs["network_mode"] == "none"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["read_only"] is True
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["mem_limit"] == "512m"
    assert kwargs["detach"] is True
    assert kwargs["remove"] is False

    assert str(tmp_path) in kwargs["volumes"]
    assert kwargs["volumes"][str(tmp_path)] == {
        "bind": "/workspace",
        "mode": "ro",
    }

    assert "/tmp" in kwargs["tmpfs"]
    assert kwargs["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"

    assert container.removed is True


def test_docker_sandbox_reports_test_failure(tmp_path):
    container = FakeContainer(status_code=1, logs=b"test failed")
    client = FakeDockerClient(container)

    runner = DockerSandboxRunner(
        repo_root=tmp_path,
        client=client,
    )

    result = runner.run_tests()

    assert result.passed is False
    assert result.returncode == 1
    assert "test failed" in result.output
    assert container.removed is True


def test_docker_sandbox_rejects_invalid_repo_path(tmp_path):
    missing_dir = tmp_path / "missing"

    runner = DockerSandboxRunner(repo_root=missing_dir)

    result = runner.run_tests()

    assert result.passed is False
    assert result.returncode is None
    assert "not a directory" in result.output


def test_docker_sandbox_handles_timeout(tmp_path):
    container = TimeoutContainer()
    client = FakeDockerClient(container)

    runner = DockerSandboxRunner(
        repo_root=tmp_path,
        client=client,
        timeout_seconds=1,
    )

    result = runner.run_tests()

    assert result.passed is False
    assert result.timed_out is True
    assert container.stopped is True
    assert container.removed is True