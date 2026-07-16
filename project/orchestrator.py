from __future__ import annotations

import codecs
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from project.core.cleanup import apply_cleanup
from project.core.mutator import apply_mutation
from project.core.writer import (
    EphemeralFileTransaction,
    FileTransactionError,
)
from project.discovery.schema import (
    RemediationTarget,
)
from project.discovery.service import (
    DiscoveryResult,
)
from project.validation.runner import (
    ValidationResult,
)


class OrchestrationError(RuntimeError):
    """
    Base exception for fatal V8 orchestration
    failures.
    """


class BaselineValidationError(
    OrchestrationError
):
    """
    Raised when the untouched repository test
    suite does not pass.
    """

    def __init__(
        self,
        result: ValidationResult,
    ) -> None:
        self.result = result

        super().__init__(
            "Baseline validation failed; "
            "refusing to mutate the repository."
        )


class TargetStatus(str, Enum):
    PREPARED = "prepared"

    VALIDATION_FAILED = (
        "validation_failed"
    )

    PREPARATION_FAILED = (
        "preparation_failed"
    )

    NO_CHANGES = "no_changes"


@dataclass(frozen=True)
class PreparedFileChange:
    """
    One validated replacement ready for
    GitHub delivery.
    """

    path: str
    updated_source: str
    updated_bytes: bytes
    original_sha256: str
    updated_sha256: str


@dataclass(frozen=True)
class TargetOutcome:
    """
    Result of preparing and validating one
    remediation target.
    """

    target: RemediationTarget
    status: TargetStatus

    files: tuple[
        PreparedFileChange,
        ...,
    ] = ()

    validation: (
        ValidationResult | None
    ) = None

    error: str | None = None

    @property
    def changed_files(
        self,
    ) -> dict[str, str]:
        """
        Return the complete source mapping
        expected by V6 delivery.
        """
        return {
            change.path: (
                change.updated_source
            )
            for change in self.files
        }

    @property
    def prepared(self) -> bool:
        return (
            self.status
            is TargetStatus.PREPARED
        )


@dataclass(frozen=True)
class OrchestrationResult:
    """
    Audit result for one independent V8
    preparation run.
    """

    discovery: DiscoveryResult

    baseline_validation: (
        ValidationResult | None
    )

    outcomes: tuple[
        TargetOutcome,
        ...,
    ]

    @property
    def prepared_targets(
        self,
    ) -> tuple[
        TargetOutcome,
        ...,
    ]:
        return tuple(
            outcome
            for outcome in self.outcomes
            if outcome.prepared
        )

    @property
    def failed_targets(
        self,
    ) -> tuple[
        TargetOutcome,
        ...,
    ]:
        return tuple(
            outcome
            for outcome in self.outcomes
            if not outcome.prepared
        )


ValidationCallable = Callable[
    [],
    ValidationResult,
]


class RemediationOrchestrator:
    """
    Prepare independently validated remediations
    without leaving local changes.

    Flow:

    1. Run V7 discovery.
    2. If no targets exist, stop without testing.
    3. Validate the untouched repository once.
    4. Build all replacements for one flag in memory.
    5. Apply the target as one temporary transaction.
    6. Validate the mutated repository.
    7. Restore the local repository even when valid.

    """

    def __init__(
        self,
        repo_root: Path,
        discovery_service,
        validate_repository: (
            ValidationCallable
        ),
    ) -> None:
        self.repo_root = (
            repo_root.resolve()
        )

        self.discovery_service = (
            discovery_service
        )

        self.validate_repository = (
            validate_repository
        )

        if not self.repo_root.is_dir():
            raise ValueError(
                "Repository root is not "
                "a directory: "
                f"{self.repo_root}"
            )

        if not callable(
            validate_repository
        ):
            raise TypeError(
                "validate_repository must "
                "be callable."
            )

    def run(
        self,
        business_context: str,
    ) -> OrchestrationResult:
        discovery = (
            self.discovery_service
            .discover(business_context)
        )

        if not discovery.plan.targets:
            return OrchestrationResult(
                discovery=discovery,
                baseline_validation=None,
                outcomes=(),
            )

        baseline = self._run_validation(
            stage="baseline"
        )

        if not baseline.passed:
            raise BaselineValidationError(
                baseline
            )

        outcomes: list[
            TargetOutcome
        ] = []

        for target in (
            discovery.plan.targets
        ):
            outcomes.append(
                self._prepare_target(
                    target=target,
                    repository_allowlist=(
                        discovery
                        .repository_allowlist
                    ),
                )
            )

        return OrchestrationResult(
            discovery=discovery,
            baseline_validation=baseline,
            outcomes=tuple(outcomes),
        )

    def _prepare_target(
        self,
        *,
        target: RemediationTarget,
        repository_allowlist: (
            frozenset[str]
        ),
    ) -> TargetOutcome:
        try:
            (
                changes,
                unchanged_paths,
            ) = self._build_file_changes(
                target=target,
                repository_allowlist=(
                    repository_allowlist
                ),
            )

        except Exception as error:
            return TargetOutcome(
                target=target,
                status=(
                    TargetStatus
                    .PREPARATION_FAILED
                ),
                error=str(error),
            )

        if unchanged_paths:
            return TargetOutcome(
                target=target,
                status=(
                    TargetStatus
                    .NO_CHANGES
                ),
                error=(
                    "The deterministic "
                    "transformer made no "
                    "change in: "
                    + ", ".join(
                        unchanged_paths
                    )
                ),
            )

        replacements = {
            change.path: (
                change.updated_bytes
            )
            for change in changes
        }

        try:
            transaction = (
                EphemeralFileTransaction(
                    self.repo_root,
                    replacements,
                )
            )

        except (
            TypeError,
            ValueError,
        ) as error:
            return TargetOutcome(
                target=target,
                status=(
                    TargetStatus
                    .PREPARATION_FAILED
                ),
                error=str(error),
            )

        try:
            with transaction:
                validation = (
                    self._run_validation(
                        stage=(
                            "post-mutation:"
                            f"{target.flag_name}"
                        )
                    )
                )

                if not validation.passed:
                    return TargetOutcome(
                        target=target,
                        status=(
                            TargetStatus
                            .VALIDATION_FAILED
                        ),
                        validation=validation,
                        error=(
                            "Post-mutation "
                            "validation failed."
                        ),
                    )

                return TargetOutcome(
                    target=target,
                    status=(
                        TargetStatus.PREPARED
                    ),
                    files=changes,
                    validation=validation,
                )

        except FileTransactionError as error:
            raise OrchestrationError(
                "The local repository transaction "
                "failed; manual inspection may "
                "be required."
            ) from error

    def _build_file_changes(
        self,
        *,
        target: RemediationTarget,
        repository_allowlist: (
            frozenset[str]
        ),
    ) -> tuple[
        tuple[
            PreparedFileChange,
            ...,
        ],
        tuple[str, ...],
    ]:
        normalized_paths = tuple(
            sorted(
                target.files_affected
            )
        )

        if (
            len(set(normalized_paths))
            != len(normalized_paths)
        ):
            raise ValueError(
                f"Target {target.flag_name!r} "
                "contains duplicate file paths."
            )

        unknown_paths = (
            set(normalized_paths)
            - repository_allowlist
        )

        if unknown_paths:
            raise ValueError(
                f"Target {target.flag_name!r} "
                "contains paths outside the "
                "repository allowlist: "
                f"{sorted(unknown_paths)}"
            )

        changes: list[
            PreparedFileChange
        ] = []

        unchanged: list[str] = []

        for relative_path in (
            normalized_paths
        ):
            absolute_path = (
                self._resolve_allowlisted_file(
                    relative_path
                )
            )

            original_bytes = (
                absolute_path.read_bytes()
            )

            (
                source,
                had_bom,
            ) = _decode_utf8_python_source(
                original_bytes,
                relative_path,
            )

            mutated_source = apply_mutation(
                source,
                target.flag_name,
                target.final_state,
            )

            if mutated_source == source:
                unchanged.append(
                    relative_path
                )
                continue

            cleaned_source = apply_cleanup(
                mutated_source
            )

            updated_bytes = (
                _encode_utf8_python_source(
                    cleaned_source,
                    had_bom=had_bom,
                )
            )

            if (
                updated_bytes
                == original_bytes
            ):
                unchanged.append(
                    relative_path
                )
                continue

            github_source = (
                "\ufeff" + cleaned_source
                if had_bom
                else cleaned_source
            )

            changes.append(
                PreparedFileChange(
                    path=relative_path,
                    updated_source=(
                        github_source
                    ),
                    updated_bytes=(
                        updated_bytes
                    ),
                    original_sha256=(
                        _sha256(
                            original_bytes
                        )
                    ),
                    updated_sha256=(
                        _sha256(
                            updated_bytes
                        )
                    ),
                )
            )

        return (
            tuple(changes),
            tuple(unchanged),
        )

    def _resolve_allowlisted_file(
        self,
        relative_path: str,
    ) -> Path:
        pure_path = PurePosixPath(
            relative_path
        )

        if (
            pure_path.is_absolute()
            or not pure_path.parts
            or any(
                part in {
                    "",
                    ".",
                    "..",
                }
                for part
                in pure_path.parts
            )
            or (
                pure_path
                .suffix
                .lower()
                != ".py"
            )
        ):
            raise ValueError(
                "Unsafe Python path: "
                f"{relative_path!r}"
            )

        candidate = (
            self.repo_root.joinpath(
                *pure_path.parts
            )
        )

        current = self.repo_root

        for part in pure_path.parts:
            current = current / part

            if current.is_symlink():
                raise ValueError(
                    "Symbolic-link paths "
                    "are not supported: "
                    f"{relative_path}"
                )

        resolved = candidate.resolve()

        try:
            resolved.relative_to(
                self.repo_root
            )

        except ValueError as error:
            raise ValueError(
                "Path resolves outside "
                "repository root: "
                f"{relative_path}"
            ) from error

        if not resolved.is_file():
            raise ValueError(
                "Target path is not "
                "a file: "
                f"{relative_path}"
            )

        return resolved

    def _run_validation(
        self,
        *,
        stage: str,
    ) -> ValidationResult:
        try:
            result = (
                self.validate_repository()
            )

        except Exception as error:
            raise OrchestrationError(
                "Validation callable raised "
                f"during {stage}: {error}"
            ) from error

        if not isinstance(
            result,
            ValidationResult,
        ):
            raise OrchestrationError(
                "Validation callable returned "
                "an invalid result during "
                f"{stage}."
            )

        return result


def _decode_utf8_python_source(
    raw: bytes,
    path: str,
) -> tuple[str, bool]:
    had_bom = raw.startswith(
        codecs.BOM_UTF8
    )

    payload = (
        raw[
            len(codecs.BOM_UTF8) :
        ]
        if had_bom
        else raw
    )

    try:
        return (
            payload.decode("utf-8"),
            had_bom,
        )

    except UnicodeDecodeError as error:
        raise ValueError(
            "Python file is not valid "
            f"UTF-8: {path}"
        ) from error


def _encode_utf8_python_source(
    source: str,
    *,
    had_bom: bool,
) -> bytes:
    encoded = source.encode("utf-8")

    if had_bom:
        return (
            codecs.BOM_UTF8
            + encoded
        )

    return encoded


def _sha256(
    content: bytes,
) -> str:
    return hashlib.sha256(
        content
    ).hexdigest()