from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from project.delivery.github_ops import (
    PullRequestResult,
)
from project.orchestrator import (
    OrchestrationResult,
    TargetOutcome,
)


class PipelineError(RuntimeError):
    """
    Raised when a fatal pipeline invariant
    is violated.
    """


class DeliveryStatus(str, Enum):
    DELIVERED = "delivered"
    REUSED = "reused"
    NOT_PREPARED = "not_prepared"
    FAILED = "failed"


@dataclass(frozen=True)
class TargetDeliveryOutcome:
    """
    Delivery result for one discovered target.
    """

    preparation: TargetOutcome
    status: DeliveryStatus

    pull_request: (
        PullRequestResult | None
    ) = None

    error: str | None = None

    @property
    def successful(self) -> bool:
        return self.status in {
            DeliveryStatus.DELIVERED,
            DeliveryStatus.REUSED,
        }


@dataclass(frozen=True)
class PipelineResult:
    """
    Complete V8 preparation and delivery report.
    """

    orchestration: OrchestrationResult

    deliveries: tuple[
        TargetDeliveryOutcome,
        ...,
    ]

    @property
    def successful_deliveries(
        self,
    ) -> tuple[
        TargetDeliveryOutcome,
        ...,
    ]:
        return tuple(
            outcome
            for outcome in self.deliveries
            if outcome.successful
        )

    @property
    def failed_deliveries(
        self,
    ) -> tuple[
        TargetDeliveryOutcome,
        ...,
    ]:
        return tuple(
            outcome
            for outcome in self.deliveries
            if (
                outcome.status
                is DeliveryStatus.FAILED
            )
        )

    @property
    def not_prepared_targets(
        self,
    ) -> tuple[
        TargetDeliveryOutcome,
        ...,
    ]:
        return tuple(
            outcome
            for outcome in self.deliveries
            if (
                outcome.status
                is DeliveryStatus.NOT_PREPARED
            )
        )

    @property
    def has_failures(self) -> bool:
        return bool(
            self.failed_deliveries
            or self.not_prepared_targets
        )


class OrchestratorProtocol(Protocol):
    def run(
        self,
        business_context: str,
    ) -> OrchestrationResult:
        ...


class DeliveryProtocol(Protocol):
    def create_remediation_pr(
        self,
        changed_files: dict[str, str],
        flag_name: str,
        base_branch: str | None = None,
    ) -> PullRequestResult:
        ...


class RemediationPipeline:
    """
    Run the preparation orchestrator and deliver
    each validated target through GitHub.

    Targets are independent:

    - Non-prepared targets are skipped.
    - A delivery failure is recorded.
    - Later targets are still attempted.
    - Existing open PRs are reported as reused.
    """

    def __init__(
        self,
        *,
        orchestrator: OrchestratorProtocol,
        delivery: DeliveryProtocol,
        base_branch: str | None = None,
    ) -> None:
        if not hasattr(
            orchestrator,
            "run",
        ):
            raise TypeError(
                "orchestrator must provide "
                "a run() method."
            )

        if not hasattr(
            delivery,
            "create_remediation_pr",
        ):
            raise TypeError(
                "delivery must provide "
                "create_remediation_pr()."
            )

        if base_branch is not None:
            if not isinstance(
                base_branch,
                str,
            ):
                raise TypeError(
                    "base_branch must be "
                    "a string or None."
                )

            base_branch = (
                base_branch.strip()
            )

            if not base_branch:
                raise ValueError(
                    "base_branch cannot "
                    "be blank."
                )

        self.orchestrator = orchestrator
        self.delivery = delivery
        self.base_branch = base_branch

    def run(
        self,
        business_context: str,
    ) -> PipelineResult:
        orchestration = (
            self.orchestrator.run(
                business_context
            )
        )

        if not isinstance(
            orchestration,
            OrchestrationResult,
        ):
            raise PipelineError(
                "The orchestrator returned "
                "an invalid result."
            )

        deliveries: list[
            TargetDeliveryOutcome
        ] = []

        for preparation in (
            orchestration.outcomes
        ):
            deliveries.append(
                self._deliver_target(
                    preparation
                )
            )

        return PipelineResult(
            orchestration=orchestration,
            deliveries=tuple(deliveries),
        )

    def _deliver_target(
        self,
        preparation: TargetOutcome,
    ) -> TargetDeliveryOutcome:
        if not preparation.prepared:
            return TargetDeliveryOutcome(
                preparation=preparation,
                status=(
                    DeliveryStatus
                    .NOT_PREPARED
                ),
                error=(
                    preparation.error
                    or (
                        "Target was not "
                        "prepared successfully."
                    )
                ),
            )

        changed_files = (
            preparation.changed_files
        )

        if not changed_files:
            raise PipelineError(
                "Prepared target "
                f"{preparation.target.flag_name!r} "
                "contains no changed files."
            )

        try:
            pull_request = (
                self.delivery
                .create_remediation_pr(
                    changed_files=(
                        changed_files
                    ),
                    flag_name=(
                        preparation
                        .target
                        .flag_name
                    ),
                    base_branch=(
                        self.base_branch
                    ),
                )
            )

        except Exception as error:
            return TargetDeliveryOutcome(
                preparation=preparation,
                status=(
                    DeliveryStatus.FAILED
                ),
                error=(
                    f"{error.__class__.__name__}: "
                    f"{error}"
                ),
            )

        if not isinstance(
            pull_request,
            PullRequestResult,
        ):
            raise PipelineError(
                "GitHub delivery returned "
                "an invalid result for "
                f"{preparation.target.flag_name!r}."
            )

        status = (
            DeliveryStatus.REUSED
            if (
                pull_request
                .reused_existing_pr
            )
            else DeliveryStatus.DELIVERED
        )

        return TargetDeliveryOutcome(
            preparation=preparation,
            status=status,
            pull_request=pull_request,
        )