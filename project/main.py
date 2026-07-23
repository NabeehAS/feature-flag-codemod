from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from github import GithubException

from project.delivery.github_ops import (
    GitOpsDelivery,
)
from project.discovery.llm import (
    DiscoveryError,
    OpenAIFeatureFlagDiscovery,
)
from project.discovery.service import (
    DiscoveryService,
)
from project.orchestrator import (
    BaselineValidationError,
    OrchestrationError,
    OrchestrationResult,
    RemediationOrchestrator,
    TargetOutcome,
)
from project.pipeline import (
    PipelineError,
    PipelineResult,
    RemediationPipeline,
)
from project.validation.runner import (
    LocalTestRunner,
    ValidationResult,
)
from project.validation.sandbox import (
    DockerSandboxRunner,
)


EXIT_SUCCESS = 0
EXIT_TARGET_FAILURE = 1
EXIT_FATAL = 2
EXIT_INTERRUPTED = 130

MAX_CONTEXT_BYTES = 100_000
MAX_CONTEXT_CHARACTERS = 20_000

MAX_REPORT_OUTPUT_CHARACTERS = (
    50_000
)


class ConfigurationError(ValueError):
    """
    Raised when the production CLI
    configuration is invalid.
    """


@dataclass(frozen=True)
class AppConfig:
    repo_root: Path
    context_file: Path

    create_pr: bool

    github_repository: (
        str | None
    )

    base_branch: str | None
    model: str

    sandbox: bool
    docker_image: str
    timeout_seconds: int

    json_report: Path | None
    max_diff_lines: int

    @property
    def mode(self) -> str:
        return (
            "delivery"
            if self.create_pr
            else "dry_run"
        )


def parse_config(
    argv: Sequence[str] | None = None,
    *,
    environ: (
        Mapping[str, str] | None
    ) = None,
) -> AppConfig:
    environment = (
        os.environ
        if environ is None
        else environ
    )

    parser = argparse.ArgumentParser(
        description=(
            "Discover, validate, and "
            "optionally deliver deprecated "
            "feature-flag remediations."
        )
    )

    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Target Python repository.",
    )

    parser.add_argument(
        "--context-file",
        type=Path,
        required=True,
        help=(
            "UTF-8 file containing "
            "feature-flag deprecation "
            "evidence."
        ),
    )

    parser.add_argument(
        "--create-pr",
        action="store_true",
        help=(
            "Deliver validated targets "
            "to GitHub. Without this "
            "option, the command performs "
            "a dry run and makes no "
            "GitHub writes."
        ),
    )

    parser.add_argument(
        "--github-repository",
        help=(
            "GitHub repository in owner/name "
            "form. Defaults to "
            "GITHUB_REPOSITORY."
        ),
    )

    parser.add_argument(
        "--base-branch",
        help=(
            "Optional GitHub base branch "
            "override."
        ),
    )

    parser.add_argument(
        "--model",
        help=(
            "OpenAI model. Defaults to "
            "OPENAI_MODEL or gpt-5-mini."
        ),
    )

    parser.add_argument(
        "--sandbox",
        action="store_true",
        help=(
            "Run baseline and post-mutation "
            "validation in Docker."
        ),
    )

    parser.add_argument(
        "--docker-image",
        default=(
            "feature-flag-undertaker-"
            "sandbox:py312"
        ),
        help=(
            "Docker image used when "
            "--sandbox is enabled."
        ),
    )

    parser.add_argument(
        "--timeout-seconds",
        type=_positive_int,
        default=300,
        help=(
            "Maximum duration of each "
            "validation run."
        ),
    )

    parser.add_argument(
        "--json-report",
        type=Path,
        help=(
            "Optional path for an atomic "
            "machine-readable JSON report."
        ),
    )

    parser.add_argument(
        "--max-diff-lines",
        type=_non_negative_int,
        default=400,
        help=(
            "Maximum unified-diff lines "
            "retained per prepared target. "
            "Use 0 to disable diff generation."
        ),
    )

    args = parser.parse_args(argv)

    model = (
        args.model
        or environment.get(
            "OPENAI_MODEL"
        )
        or "gpt-5-mini"
    ).strip()

    if not model:
        raise ConfigurationError(
            "model cannot be blank."
        )

    repository = (
        args.github_repository
        or environment.get(
            "GITHUB_REPOSITORY"
        )
    )

    if repository is not None:
        repository = (
            repository.strip()
        )

        _validate_github_repository(
            repository
        )

    if (
        args.create_pr
        and repository is None
    ):
        raise ConfigurationError(
            "--create-pr requires "
            "--github-repository or "
            "GITHUB_REPOSITORY."
        )

    base_branch = args.base_branch

    if base_branch is not None:
        base_branch = (
            base_branch.strip()
        )

        if not base_branch:
            raise ConfigurationError(
                "base_branch cannot "
                "be blank."
            )

    docker_image = (
        args.docker_image.strip()
    )

    if not docker_image:
        raise ConfigurationError(
            "docker_image cannot "
            "be blank."
        )

    return AppConfig(
        repo_root=(
            args.repo_root
            .expanduser()
            .resolve()
        ),
        context_file=(
            args.context_file
            .expanduser()
            .resolve()
        ),
        create_pr=bool(
            args.create_pr
        ),
        github_repository=repository,
        base_branch=base_branch,
        model=model,
        sandbox=bool(args.sandbox),
        docker_image=docker_image,
        timeout_seconds=(
            args.timeout_seconds
        ),
        json_report=(
            args.json_report
            .expanduser()
            .resolve()
            if args.json_report
            is not None
            else None
        ),
        max_diff_lines=(
            args.max_diff_lines
        ),
    )


def load_business_context(
    path: Path,
) -> str:
    if not path.is_file():
        raise ConfigurationError(
            "Context file is not a "
            f"readable file: {path}"
        )

    try:
        size = path.stat().st_size

    except OSError as error:
        raise ConfigurationError(
            "Could not inspect context "
            f"file {path}: {error}"
        ) from error

    if size > MAX_CONTEXT_BYTES:
        raise ConfigurationError(
            "Context file exceeds the "
            "configured byte limit of "
            f"{MAX_CONTEXT_BYTES}."
        )

    try:
        context = path.read_text(
            encoding="utf-8-sig"
        ).strip()

    except (
        OSError,
        UnicodeError,
    ) as error:
        raise ConfigurationError(
            "Could not read UTF-8 "
            f"context file {path}: "
            f"{error}"
        ) from error

    if not context:
        raise ConfigurationError(
            "Context file cannot be empty."
        )

    if (
        len(context)
        > MAX_CONTEXT_CHARACTERS
    ):
        raise ConfigurationError(
            "Context file exceeds the "
            "configured character limit "
            f"of {MAX_CONTEXT_CHARACTERS}."
        )

    return context


def build_validation_callable(
    config: AppConfig,
) -> Callable[
    [],
    ValidationResult,
]:
    if not config.repo_root.is_dir():
        raise ConfigurationError(
            "Repository root is not "
            f"a directory: "
            f"{config.repo_root}"
        )

    if config.sandbox:
        runner = DockerSandboxRunner(
            repo_root=config.repo_root,
            image=config.docker_image,
            timeout_seconds=(
                config.timeout_seconds
            ),
        )

        return runner.run_tests

    runner = LocalTestRunner(
        repo_root=config.repo_root,
        timeout_seconds=(
            config.timeout_seconds
        ),
    )

    return runner.run


def build_orchestrator(
    config: AppConfig,
    *,
    environ: Mapping[str, str],
) -> RemediationOrchestrator:
    api_key = _require_secret(
        environ,
        "OPENAI_API_KEY",
    )

    discovery_client = (
        OpenAIFeatureFlagDiscovery(
            api_key=api_key,
            model=config.model,
        )
    )

    discovery_service = (
        DiscoveryService(
            repo_root=(
                config.repo_root
            ),
            discovery_client=(
                discovery_client
            ),
        )
    )

    return RemediationOrchestrator(
        repo_root=config.repo_root,
        discovery_service=(
            discovery_service
        ),
        validate_repository=(
            build_validation_callable(
                config
            )
        ),
    )


def build_pipeline(
    config: AppConfig,
    *,
    orchestrator: (
        RemediationOrchestrator
    ),
    environ: Mapping[str, str],
) -> RemediationPipeline:
    if not config.create_pr:
        raise ConfigurationError(
            "GitHub delivery cannot "
            "be built in dry-run mode."
        )

    if (
        config.github_repository
        is None
    ):
        raise ConfigurationError(
            "GitHub repository is missing."
        )

    github_token = _require_secret(
        environ,
        "GITHUB_TOKEN",
    )

    delivery = GitOpsDelivery(
        token=github_token,
        repo_name=(
            config.github_repository
        ),
    )

    return RemediationPipeline(
        orchestrator=orchestrator,
        delivery=delivery,
        base_branch=config.base_branch,
    )


def execute(
    config: AppConfig,
    *,
    environ: (
        Mapping[str, str] | None
    ) = None,
    stdout: TextIO | None = None,
) -> int:
    environment = (
        os.environ
        if environ is None
        else environ
    )

    output = (
        sys.stdout
        if stdout is None
        else stdout
    )

    business_context = (
        load_business_context(
            config.context_file
        )
    )

    orchestrator = (
        build_orchestrator(
            config,
            environ=environment,
        )
    )

    if config.create_pr:
        pipeline = build_pipeline(
            config,
            orchestrator=orchestrator,
            environ=environment,
        )

        result = pipeline.run(
            business_context
        )

        report = build_pipeline_report(
            config,
            result,
        )

        _print_pipeline_summary(
            result,
            output,
        )

        exit_code = (
            EXIT_TARGET_FAILURE
            if result.has_failures
            else EXIT_SUCCESS
        )

    else:
        result = orchestrator.run(
            business_context
        )

        report = (
            build_orchestration_report(
                config,
                result,
            )
        )

        _print_orchestration_summary(
            config,
            result,
            output,
        )

        exit_code = (
            EXIT_TARGET_FAILURE
            if result.failed_targets
            else EXIT_SUCCESS
        )

    if config.json_report is not None:
        write_json_report(
            config.json_report,
            report,
        )

        print(
            "JSON report: "
            f"{config.json_report}",
            file=output,
        )

    return exit_code


def main(
    argv: Sequence[str] | None = None,
) -> int:
    try:
        config = parse_config(argv)
        return execute(config)

    except BaselineValidationError as error:
        print(
            f"Fatal: {error}",
            file=sys.stderr,
        )

        if error.result.output:
            print(
                error.result.output,
                file=sys.stderr,
            )

        return EXIT_FATAL

    except (
        ConfigurationError,
        DiscoveryError,
        OrchestrationError,
        PipelineError,
        GithubException,
        OSError,
    ) as error:
        print(
            "Fatal: "
            f"{error.__class__.__name__}: "
            f"{error}",
            file=sys.stderr,
        )

        return EXIT_FATAL

    except KeyboardInterrupt:
        print(
            "Interrupted.",
            file=sys.stderr,
        )

        return EXIT_INTERRUPTED


def build_orchestration_report(
    config: AppConfig,
    result: OrchestrationResult,
) -> dict[str, Any]:
    outcomes = [
        _preparation_to_dict(
            config.repo_root,
            outcome,
            max_diff_lines=(
                config.max_diff_lines
            ),
        )
        for outcome
        in result.outcomes
    ]

    return {
        "schema_version": 1,
        "mode": "dry_run",
        "repository": str(
            config.repo_root
        ),
        "model": config.model,
        "validation_mode": (
            "docker"
            if config.sandbox
            else "local"
        ),
        "baseline_validation": (
            _validation_to_dict(
                result
                .baseline_validation
            )
        ),
        "discovery": (
            _discovery_to_dict(
                result.discovery
            )
        ),
        "outcomes": outcomes,
        "summary": {
            "targets": len(
                result.outcomes
            ),
            "prepared": len(
                result
                .prepared_targets
            ),
            "failed": len(
                result
                .failed_targets
            ),
        },
    }


def build_pipeline_report(
    config: AppConfig,
    result: PipelineResult,
) -> dict[str, Any]:
    report = (
        build_orchestration_report(
            config,
            result.orchestration,
        )
    )

    report["mode"] = "delivery"

    report[
        "github_repository"
    ] = config.github_repository

    report[
        "base_branch"
    ] = config.base_branch

    report["deliveries"] = [
        {
            "flag_name": (
                item
                .preparation
                .target
                .flag_name
            ),
            "status": (
                item.status.value
            ),
            "error": item.error,
            "pull_request": (
                {
                    "url": (
                        item
                        .pull_request
                        .url
                    ),
                    "branch_name": (
                        item
                        .pull_request
                        .branch_name
                    ),
                    "commit_sha": (
                        item
                        .pull_request
                        .commit_sha
                    ),
                    "pr_number": (
                        item
                        .pull_request
                        .pr_number
                    ),
                    "reused_existing_pr": (
                        item
                        .pull_request
                        .reused_existing_pr
                    ),
                }
                if item.pull_request
                is not None
                else None
            ),
        }
        for item
        in result.deliveries
    ]

    report["summary"].update(
        {
            "delivered_or_reused": len(
                result
                .successful_deliveries
            ),
            "delivery_failed": len(
                result
                .failed_deliveries
            ),
            "not_prepared": len(
                result
                .not_prepared_targets
            ),
        }
    )

    return report


def write_json_report(
    path: Path,
    report: Mapping[str, Any],
) -> None:
    destination = (
        path.expanduser().resolve()
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path: (
        Path | None
    ) = None

    serialized = json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"

    try:
        with (
            tempfile
            .NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=(
                    destination.parent
                ),
                prefix=(
                    f".{destination.name}."
                ),
                suffix=".tmp",
                delete=False,
            )
        ) as temporary_file:
            temporary_file.write(
                serialized
            )

            temporary_file.flush()

            os.fsync(
                temporary_file.fileno()
            )

            temporary_path = Path(
                temporary_file.name
            )

        os.replace(
            temporary_path,
            destination,
        )

        temporary_path = None

    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(
                    missing_ok=True
                )

            except OSError:
                pass


def _discovery_to_dict(
    discovery: Any,
) -> dict[str, Any]:
    return {
        "targets": [
            target.model_dump(
                mode="json"
            )
            for target
            in discovery.plan.targets
        ],
        "candidate_flags": [
            candidate.flag_name
            for candidate
            in discovery.candidates
        ],
        "repository_allowlist": (
            sorted(
                discovery
                .repository_allowlist
            )
        ),
    }


def _preparation_to_dict(
    repo_root: Path,
    outcome: TargetOutcome,
    *,
    max_diff_lines: int,
) -> dict[str, Any]:
    return {
        "flag_name": (
            outcome
            .target
            .flag_name
        ),
        "final_state": (
            outcome
            .target
            .final_state
        ),
        "status": (
            outcome.status.value
        ),
        "error": outcome.error,
        "validation": (
            _validation_to_dict(
                outcome.validation
            )
        ),
        "files": [
            {
                "path": change.path,
                "original_sha256": (
                    change
                    .original_sha256
                ),
                "updated_sha256": (
                    change
                    .updated_sha256
                ),
            }
            for change
            in outcome.files
        ],
        "diff": _build_target_diff(
            repo_root,
            outcome,
            max_lines=max_diff_lines,
        ),
    }


def _validation_to_dict(
    result: ValidationResult | None,
) -> dict[str, Any] | None:
    if result is None:
        return None

    return {
        "passed": result.passed,
        "output": _truncate_text(
            result.output,
            (
                MAX_REPORT_OUTPUT_CHARACTERS
            ),
        ),
        "returncode": (
            result.returncode
        ),
        "timed_out": (
            result.timed_out
        ),
    }


def _build_target_diff(
    repo_root: Path,
    outcome: TargetOutcome,
    *,
    max_lines: int,
) -> str | None:
    if (
        not outcome.prepared
        or max_lines == 0
    ):
        return None

    diff_lines: list[str] = []

    for change in outcome.files:
        path = repo_root.joinpath(
            *Path(change.path).parts
        )

        try:
            original_text = (
                path.read_bytes()
                .decode("utf-8-sig")
            )

        except (
            OSError,
            UnicodeError,
        ) as error:
            diff_lines.append(
                "[diff unavailable for "
                f"{change.path}: "
                f"{error}]"
            )

            continue

        updated_text = (
            change
            .updated_source
            .removeprefix("\ufeff")
        )

        diff_lines.extend(
            difflib.unified_diff(
                original_text.splitlines(),
                updated_text.splitlines(),
                fromfile=(
                    f"a/{change.path}"
                ),
                tofile=(
                    f"b/{change.path}"
                ),
                lineterm="",
            )
        )

        if (
            len(diff_lines)
            > max_lines
        ):
            break

    if len(diff_lines) > max_lines:
        omitted = (
            len(diff_lines)
            - max_lines
        )

        diff_lines = (
            diff_lines[:max_lines]
        )

        diff_lines.append(
            "... [diff truncated; "
            f"at least {omitted} "
            "additional line(s)]"
        )

    return "\n".join(diff_lines)


def _print_orchestration_summary(
    config: AppConfig,
    result: OrchestrationResult,
    output: TextIO,
) -> None:
    print(
        "Mode: dry run "
        "(no GitHub writes)",
        file=output,
    )

    print(
        f"Model: {config.model}",
        file=output,
    )

    print(
        "Discovered targets: "
        f"{len(result.outcomes)}",
        file=output,
    )

    if (
        result.baseline_validation
        is not None
    ):
        print(
            "Baseline validation: "
            "passed",
            file=output,
        )

    for outcome in result.outcomes:
        print(
            "- "
            f"{outcome.target.flag_name}: "
            f"{outcome.status.value}",
            file=output,
        )

        if outcome.error:
            print(
                f"  Error: "
                f"{outcome.error}",
                file=output,
            )

        diff = _build_target_diff(
            config.repo_root,
            outcome,
            max_lines=(
                config.max_diff_lines
            ),
        )

        if diff:
            print(
                diff,
                file=output,
            )


def _print_pipeline_summary(
    result: PipelineResult,
    output: TextIO,
) -> None:
    print(
        "Mode: GitHub delivery",
        file=output,
    )

    print(
        "Discovered targets: "
        f"{len(result.deliveries)}",
        file=output,
    )

    for outcome in result.deliveries:
        print(
            "- "
            f"{outcome.preparation.target.flag_name}: "
            f"{outcome.status.value}",
            file=output,
        )

        if (
            outcome.pull_request
            is not None
        ):
            print(
                "  PR: "
                f"{outcome.pull_request.url}",
                file=output,
            )

        if outcome.error:
            print(
                f"  Error: {outcome.error}",
                file=output,
            )


def _truncate_text(
    value: str,
    limit: int,
) -> str:
    if len(value) <= limit:
        return value

    omitted = (
        len(value) - limit
    )

    return (
        value[:limit].rstrip()
        + "\n... [truncated "
        f"{omitted} character(s)]"
    )


def _require_secret(
    environ: Mapping[str, str],
    name: str,
) -> str:
    value = environ.get(
        name,
        "",
    ).strip()

    if not value:
        raise ConfigurationError(
            f"{name} is not set."
        )

    return value


def _validate_github_repository(
    repository: str,
) -> None:
    if re.fullmatch(
        (
            r"[A-Za-z0-9_.-]+/"
            r"[A-Za-z0-9_.-]+"
        ),
        repository,
    ) is None:
        raise ConfigurationError(
            "GitHub repository must "
            "use owner/name format."
        )


def _positive_int(
    value: str,
) -> int:
    number = int(value)

    if number <= 0:
        raise (
            argparse
            .ArgumentTypeError(
                "value must be positive"
            )
        )

    return number


def _non_negative_int(
    value: str,
) -> int:
    number = int(value)

    if number < 0:
        raise (
            argparse
            .ArgumentTypeError(
                "value cannot be negative"
            )
        )

    return number


if __name__ == "__main__":
    raise SystemExit(main())