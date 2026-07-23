from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import project.main as app
from project.delivery.github_ops import PullRequestResult
from project.discovery.schema import (
    DiscoveryPlan,
    EvidenceItem,
    RemediationTarget,
)
from project.discovery.service import DiscoveryResult
from project.orchestrator import (
    OrchestrationResult,
    PreparedFileChange,
    TargetOutcome,
    TargetStatus,
)
from project.pipeline import (
    DeliveryStatus,
    PipelineResult,
    TargetDeliveryOutcome,
)
from project.validation.runner import ValidationResult


class FakeOrchestrator:
    def __init__(
        self,
        result: OrchestrationResult,
    ) -> None:
        self.result = result
        self.calls: list[str] = []

    def run(
        self,
        business_context: str,
    ) -> OrchestrationResult:
        self.calls.append(
            business_context
        )
        return self.result


class FakePipeline:
    def __init__(
        self,
        result: PipelineResult,
    ) -> None:
        self.result = result
        self.calls: list[str] = []

    def run(
        self,
        business_context: str,
    ) -> PipelineResult:
        self.calls.append(
            business_context
        )
        return self.result


def target(
    flag_name: str = "OLD_FLAG",
) -> RemediationTarget:
    return RemediationTarget(
        flag_name=flag_name,
        final_state=False,
        files_affected=["app.py"],
        evidence=[
            EvidenceItem(
                source="business_context",
                quote=(
                    f"{flag_name} was "
                    "permanently disabled."
                ),
                explanation=(
                    "This establishes the "
                    "final state."
                ),
            )
        ],
    )


def prepared_outcome() -> TargetOutcome:
    source = "old()\n"

    return TargetOutcome(
        target=target(),
        status=TargetStatus.PREPARED,
        files=(
            PreparedFileChange(
                path="app.py",
                updated_source=source,
                updated_bytes=(
                    source.encode("utf-8")
                ),
                original_sha256="a" * 64,
                updated_sha256="b" * 64,
            ),
        ),
        validation=ValidationResult(
            passed=True,
            output="passed",
            returncode=0,
        ),
    )


def failed_outcome() -> TargetOutcome:
    return TargetOutcome(
        target=target(),
        status=(
            TargetStatus
            .VALIDATION_FAILED
        ),
        validation=ValidationResult(
            passed=False,
            output="regression",
            returncode=1,
        ),
        error=(
            "Post-mutation validation "
            "failed."
        ),
    )


def orchestration_result(
    *outcomes: TargetOutcome,
) -> OrchestrationResult:
    targets = [
        outcome.target
        for outcome in outcomes
    ]

    return OrchestrationResult(
        discovery=DiscoveryResult(
            plan=DiscoveryPlan(
                targets=targets
            ),
            candidates=(),
            repository_allowlist=(
                frozenset({"app.py"})
                if targets
                else frozenset()
            ),
        ),
        baseline_validation=(
            ValidationResult(
                passed=True,
                output="baseline passed",
                returncode=0,
            )
            if targets
            else None
        ),
        outcomes=tuple(outcomes),
    )


def pipeline_result(
    preparation: TargetOutcome,
    *,
    status: DeliveryStatus,
) -> PipelineResult:
    pull_request = None
    error = None

    if status in {
        DeliveryStatus.DELIVERED,
        DeliveryStatus.REUSED,
    }:
        pull_request = PullRequestResult(
            url=(
                "https://github.com/"
                "example/repo/pull/1"
            ),
            branch_name=(
                "remediation/"
                "remove-old-flag"
            ),
            commit_sha="commit-sha",
            pr_number=1,
            reused_existing_pr=(
                status
                is DeliveryStatus.REUSED
            ),
        )

    elif status is DeliveryStatus.FAILED:
        error = "GitHub unavailable"

    orchestration = (
        orchestration_result(
            preparation
        )
    )

    return PipelineResult(
        orchestration=orchestration,
        deliveries=(
            TargetDeliveryOutcome(
                preparation=preparation,
                status=status,
                pull_request=pull_request,
                error=error,
            ),
        ),
    )


def base_args(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    list[str],
]:
    repo = tmp_path / "repo"
    repo.mkdir()

    context = (
        tmp_path / "context.txt"
    )

    context.write_text(
        (
            "OLD_FLAG was "
            "permanently disabled."
        ),
        encoding="utf-8",
    )

    return (
        repo,
        context,
        [
            "--repo-root",
            str(repo),
            "--context-file",
            str(context),
        ],
    )


def test_parse_config_defaults_to_dry_run_and_environment_model(
    tmp_path: Path,
):
    repo, context, argv = (
        base_args(tmp_path)
    )

    config = app.parse_config(
        argv,
        environ={
            "OPENAI_MODEL": (
                "gpt-5-nano"
            )
        },
    )

    assert (
        config.repo_root
        == repo.resolve()
    )

    assert (
        config.context_file
        == context.resolve()
    )

    assert config.create_pr is False
    assert config.mode == "dry_run"

    assert (
        config.model
        == "gpt-5-nano"
    )


def test_create_pr_requires_repository(
    tmp_path: Path,
):
    _, _, argv = base_args(tmp_path)

    with pytest.raises(
        app.ConfigurationError,
        match="GITHUB_REPOSITORY",
    ):
        app.parse_config(
            [
                *argv,
                "--create-pr",
            ],
            environ={},
        )


def test_invalid_repository_format_is_rejected(
    tmp_path: Path,
):
    _, _, argv = base_args(tmp_path)

    with pytest.raises(
        app.ConfigurationError,
        match="owner/name",
    ):
        app.parse_config(
            [
                *argv,
                "--create-pr",
                "--github-repository",
                "invalid",
            ],
            environ={},
        )


def test_load_context_accepts_utf8_bom(
    tmp_path: Path,
):
    context = (
        tmp_path / "context.txt"
    )

    context.write_bytes(
        b"\xef\xbb\xbfEvidence.\n"
    )

    assert (
        app.load_business_context(
            context
        )
        == "Evidence."
    )


def test_empty_context_is_rejected(
    tmp_path: Path,
):
    context = (
        tmp_path / "context.txt"
    )

    context.write_text(
        " \n",
        encoding="utf-8",
    )

    with pytest.raises(
        app.ConfigurationError,
        match="empty",
    ):
        app.load_business_context(
            context
        )


def test_missing_openai_key_is_rejected(
    tmp_path: Path,
):
    _, _, argv = base_args(tmp_path)

    config = app.parse_config(
        argv,
        environ={},
    )

    with pytest.raises(
        app.ConfigurationError,
        match="OPENAI_API_KEY",
    ):
        app.build_orchestrator(
            config,
            environ={},
        )


def test_missing_github_key_is_rejected(
    tmp_path: Path,
):
    _, _, argv = base_args(tmp_path)

    config = app.parse_config(
        [
            *argv,
            "--create-pr",
            "--github-repository",
            "owner/repo",
        ],
        environ={},
    )

    with pytest.raises(
        app.ConfigurationError,
        match="GITHUB_TOKEN",
    ):
        app.build_pipeline(
            config,
            orchestrator=(
                SimpleNamespace()
            ),
            environ={},
        )


def test_dry_run_prints_diff_writes_report_and_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo, _, argv = (
        base_args(tmp_path)
    )

    (repo / "app.py").write_text(
        (
            "if OLD_FLAG:\n"
            "    new()\n"
            "else:\n"
            "    old()\n"
        ),
        encoding="utf-8",
    )

    report_path = (
        tmp_path / "report.json"
    )

    config = app.parse_config(
        [
            *argv,
            "--json-report",
            str(report_path),
        ],
        environ={},
    )

    result = orchestration_result(
        prepared_outcome()
    )

    orchestrator = (
        FakeOrchestrator(result)
    )

    monkeypatch.setattr(
        app,
        "build_orchestrator",
        lambda *args, **kwargs: (
            orchestrator
        ),
    )

    output = io.StringIO()

    exit_code = app.execute(
        config,
        environ={},
        stdout=output,
    )

    assert (
        exit_code
        == app.EXIT_SUCCESS
    )

    assert (
        "no GitHub writes"
        in output.getvalue()
    )

    assert (
        "-if OLD_FLAG:"
        in output.getvalue()
    )

    payload = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    assert (
        payload["mode"]
        == "dry_run"
    )

    assert (
        payload["summary"]
        ["prepared"]
        == 1
    )


def test_dry_run_target_failure_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, argv = base_args(tmp_path)

    config = app.parse_config(
        argv,
        environ={},
    )

    monkeypatch.setattr(
        app,
        "build_orchestrator",
        lambda *args, **kwargs: (
            FakeOrchestrator(
                orchestration_result(
                    failed_outcome()
                )
            )
        ),
    )

    assert (
        app.execute(
            config,
            environ={},
            stdout=io.StringIO(),
        )
        == app.EXIT_TARGET_FAILURE
    )


def test_delivery_prints_pr_and_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, argv = base_args(tmp_path)

    config = app.parse_config(
        [
            *argv,
            "--create-pr",
            "--github-repository",
            "owner/repo",
        ],
        environ={},
    )

    preparation = prepared_outcome()

    result = pipeline_result(
        preparation,
        status=(
            DeliveryStatus.DELIVERED
        ),
    )

    monkeypatch.setattr(
        app,
        "build_orchestrator",
        lambda *args, **kwargs: (
            SimpleNamespace()
        ),
    )

    monkeypatch.setattr(
        app,
        "build_pipeline",
        lambda *args, **kwargs: (
            FakePipeline(result)
        ),
    )

    output = io.StringIO()

    assert (
        app.execute(
            config,
            environ={},
            stdout=output,
        )
        == app.EXIT_SUCCESS
    )

    assert (
        "https://github.com/"
        "example/repo/pull/1"
        in output.getvalue()
    )


def test_delivery_failure_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, argv = base_args(tmp_path)

    config = app.parse_config(
        [
            *argv,
            "--create-pr",
            "--github-repository",
            "owner/repo",
        ],
        environ={},
    )

    preparation = prepared_outcome()

    result = pipeline_result(
        preparation,
        status=DeliveryStatus.FAILED,
    )

    monkeypatch.setattr(
        app,
        "build_orchestrator",
        lambda *args, **kwargs: (
            SimpleNamespace()
        ),
    )

    monkeypatch.setattr(
        app,
        "build_pipeline",
        lambda *args, **kwargs: (
            FakePipeline(result)
        ),
    )

    assert (
        app.execute(
            config,
            environ={},
            stdout=io.StringIO(),
        )
        == app.EXIT_TARGET_FAILURE
    )


def test_diff_limit_is_enforced(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "app.py").write_text(
        (
            "\n".join(
                f"old{i}"
                for i in range(20)
            )
            + "\n"
        ),
        encoding="utf-8",
    )

    updated = (
        "\n".join(
            f"new{i}"
            for i in range(20)
        )
        + "\n"
    )

    outcome = TargetOutcome(
        target=target(),
        status=TargetStatus.PREPARED,
        files=(
            PreparedFileChange(
                path="app.py",
                updated_source=updated,
                updated_bytes=(
                    updated.encode("utf-8")
                ),
                original_sha256="a" * 64,
                updated_sha256="b" * 64,
            ),
        ),
        validation=ValidationResult(
            passed=True,
            output="passed",
            returncode=0,
        ),
    )

    diff = app._build_target_diff(
        repo,
        outcome,
        max_lines=5,
    )

    assert diff is not None

    assert (
        len(diff.splitlines())
        == 6
    )

    assert "truncated" in diff


def test_json_report_atomically_replaces_existing_file(
    tmp_path: Path,
):
    report_path = (
        tmp_path
        / "reports"
        / "run.json"
    )

    report_path.parent.mkdir()

    report_path.write_text(
        "old",
        encoding="utf-8",
    )

    app.write_json_report(
        report_path,
        {"value": 1},
    )

    assert json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    ) == {
        "value": 1
    }

    assert not list(
        report_path
        .parent
        .glob("*.tmp")
    )


def test_validation_factory_selects_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo, _, argv = (
        base_args(tmp_path)
    )

    config = app.parse_config(
        [
            *argv,
            "--sandbox",
        ],
        environ={},
    )

    sentinel = ValidationResult(
        passed=True,
        output="sandbox",
        returncode=0,
    )

    class FakeSandbox:
        def __init__(
            self,
            **kwargs,
        ):
            assert (
                kwargs["repo_root"]
                == repo.resolve()
            )

        def run_tests(self):
            return sentinel

    monkeypatch.setattr(
        app,
        "DockerSandboxRunner",
        FakeSandbox,
    )

    validation = (
        app.build_validation_callable(
            config
        )
    )

    assert validation() is sentinel


def test_validation_output_is_bounded_in_report():
    validation = ValidationResult(
        passed=False,
        output=(
            "x"
            * (
                app
                .MAX_REPORT_OUTPUT_CHARACTERS
                + 10
            )
        ),
        returncode=1,
    )

    payload = (
        app._validation_to_dict(
            validation
        )
    )

    assert payload is not None

    assert (
        "truncated 10 character(s)"
        in payload["output"]
    )