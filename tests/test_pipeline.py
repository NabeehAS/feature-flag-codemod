from __future__ import annotations

from project.delivery.github_ops import (
    DeliveryError,
    PullRequestResult,
)
from project.discovery.schema import (
    DiscoveryPlan,
    EvidenceItem,
    RemediationTarget,
)
from project.discovery.service import (
    DiscoveryResult,
)
from project.orchestrator import (
    OrchestrationResult,
    PreparedFileChange,
    TargetOutcome,
    TargetStatus,
)
from project.pipeline import (
    DeliveryStatus,
    PipelineError,
    RemediationPipeline,
)
from project.validation.runner import (
    ValidationResult,
)


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


class FakeDelivery:
    def __init__(
        self,
        results,
    ) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def create_remediation_pr(
        self,
        changed_files,
        flag_name: str,
        base_branch: str | None = None,
    ):
        self.calls.append(
            {
                "changed_files": (
                    changed_files
                ),
                "flag_name": flag_name,
                "base_branch": (
                    base_branch
                ),
            }
        )

        result = self.results.pop(0)

        if isinstance(
            result,
            Exception,
        ):
            raise result

        return result


def target(
    flag_name: str,
    *,
    final_state: bool = False,
    files: list[str] | None = None,
) -> RemediationTarget:
    return RemediationTarget(
        flag_name=flag_name,
        final_state=final_state,
        files_affected=(
            files or ["app.py"]
        ),
        evidence=[
            EvidenceItem(
                source=(
                    "business_context"
                ),
                quote=(
                    f"{flag_name} is permanent."
                ),
                explanation=(
                    "The supplied context "
                    "states the final state."
                ),
            )
        ],
    )


def prepared_outcome(
    flag_name: str,
    *,
    path: str = "app.py",
    source: str = "print('updated')\n",
) -> TargetOutcome:
    return TargetOutcome(
        target=target(
            flag_name,
            files=[path],
        ),
        status=TargetStatus.PREPARED,
        files=(
            PreparedFileChange(
                path=path,
                updated_source=source,
                updated_bytes=(
                    source.encode("utf-8")
                ),
                original_sha256=(
                    "a" * 64
                ),
                updated_sha256=(
                    "b" * 64
                ),
            ),
        ),
        validation=ValidationResult(
            passed=True,
            output="passed",
            returncode=0,
        ),
    )


def failed_outcome(
    flag_name: str,
) -> TargetOutcome:
    return TargetOutcome(
        target=target(flag_name),
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

    allowlist = {
        path
        for outcome in outcomes
        for path
        in outcome.target.files_affected
    }

    return OrchestrationResult(
        discovery=DiscoveryResult(
            plan=DiscoveryPlan(
                targets=targets
            ),
            candidates=(),
            repository_allowlist=(
                frozenset(allowlist)
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


def pull_request(
    *,
    number: int = 1,
    reused: bool = False,
    flag_slug: str = "old-flag",
) -> PullRequestResult:
    return PullRequestResult(
        url=(
            "https://github.com/"
            "example/repo/pull/"
            f"{number}"
        ),
        branch_name=(
            "remediation/remove-"
            f"{flag_slug}"
        ),
        commit_sha=(
            f"commit-sha-{number}"
        ),
        pr_number=number,
        reused_existing_pr=reused,
    )


def test_delivers_prepared_target():
    orchestrator = FakeOrchestrator(
        orchestration_result(
            prepared_outcome(
                "OLD_FLAG"
            )
        )
    )

    delivery = FakeDelivery(
        [
            pull_request(),
        ]
    )

    result = RemediationPipeline(
        orchestrator=orchestrator,
        delivery=delivery,
    ).run(
        "OLD_FLAG is permanent."
    )

    assert orchestrator.calls == [
        "OLD_FLAG is permanent."
    ]

    assert delivery.calls == [
        {
            "changed_files": {
                "app.py": (
                    "print('updated')\n"
                ),
            },
            "flag_name": "OLD_FLAG",
            "base_branch": None,
        }
    ]

    outcome = result.deliveries[0]

    assert (
        outcome.status
        is DeliveryStatus.DELIVERED
    )

    assert (
        outcome.pull_request
        is not None
    )

    assert (
        outcome.pull_request.pr_number
        == 1
    )

    assert outcome.error is None


def test_reused_existing_pr_is_reported():
    delivery = FakeDelivery(
        [
            pull_request(
                number=7,
                reused=True,
            ),
        ]
    )

    result = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                prepared_outcome(
                    "OLD_FLAG"
                )
            )
        ),
        delivery=delivery,
    ).run("context")

    assert (
        result.deliveries[0].status
        is DeliveryStatus.REUSED
    )

    assert (
        result.deliveries[0]
        .pull_request
        .pr_number
        == 7
    )


def test_non_prepared_target_is_not_delivered():
    delivery = FakeDelivery([])

    result = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                failed_outcome(
                    "OLD_FLAG"
                )
            )
        ),
        delivery=delivery,
    ).run("context")

    assert delivery.calls == []

    outcome = result.deliveries[0]

    assert (
        outcome.status
        is DeliveryStatus.NOT_PREPARED
    )

    assert (
        outcome.preparation.status
        is TargetStatus.VALIDATION_FAILED
    )

    assert (
        outcome.error
        == (
            "Post-mutation validation "
            "failed."
        )
    )


def test_delivery_failure_does_not_block_later_target():
    first = prepared_outcome(
        "FIRST_FLAG",
        path="first.py",
        source="first()\n",
    )

    second = prepared_outcome(
        "SECOND_FLAG",
        path="second.py",
        source="second()\n",
    )

    delivery = FakeDelivery(
        [
            DeliveryError(
                "GitHub unavailable"
            ),
            pull_request(
                number=2,
                flag_slug=(
                    "second-flag"
                ),
            ),
        ]
    )

    result = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                first,
                second,
            )
        ),
        delivery=delivery,
    ).run("context")

    assert len(delivery.calls) == 2

    assert [
        outcome.status
        for outcome
        in result.deliveries
    ] == [
        DeliveryStatus.FAILED,
        DeliveryStatus.DELIVERED,
    ]

    assert (
        "GitHub unavailable"
        in result.deliveries[0].error
    )

    assert (
        result.deliveries[1]
        .pull_request
        .pr_number
        == 2
    )


def test_no_targets_makes_no_delivery_calls():
    delivery = FakeDelivery([])

    result = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result()
        ),
        delivery=delivery,
    ).run("no targets")

    assert delivery.calls == []
    assert result.deliveries == ()


def test_explicit_base_branch_is_forwarded():
    delivery = FakeDelivery(
        [
            pull_request(),
        ]
    )

    RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                prepared_outcome(
                    "OLD_FLAG"
                )
            )
        ),
        delivery=delivery,
        base_branch="develop",
    ).run("context")

    assert (
        delivery.calls[0]
        ["base_branch"]
        == "develop"
    )


def test_blank_base_branch_is_rejected():
    try:
        RemediationPipeline(
            orchestrator=FakeOrchestrator(
                orchestration_result()
            ),
            delivery=FakeDelivery([]),
            base_branch="   ",
        )

    except ValueError as error:
        assert (
            "base_branch"
            in str(error)
        )

    else:
        raise AssertionError(
            "Expected ValueError."
        )


def test_empty_prepared_mapping_is_fatal():
    invalid_prepared = TargetOutcome(
        target=target("OLD_FLAG"),
        status=TargetStatus.PREPARED,
        files=(),
        validation=ValidationResult(
            passed=True,
            output="passed",
            returncode=0,
        ),
    )

    delivery = FakeDelivery([])

    pipeline = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                invalid_prepared
            )
        ),
        delivery=delivery,
    )

    try:
        pipeline.run("context")

    except PipelineError as error:
        assert (
            "no changed files"
            in str(error)
        )

    else:
        raise AssertionError(
            "Expected PipelineError."
        )

    assert delivery.calls == []


def test_invalid_delivery_result_is_fatal():
    delivery = FakeDelivery(
        [
            "not a PullRequestResult",
        ]
    )

    pipeline = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                prepared_outcome(
                    "OLD_FLAG"
                )
            )
        ),
        delivery=delivery,
    )

    try:
        pipeline.run("context")

    except PipelineError as error:
        assert (
            "invalid result"
            in str(error)
        )

    else:
        raise AssertionError(
            "Expected PipelineError."
        )


def test_result_properties_group_outcomes():
    delivery = FakeDelivery(
        [
            pull_request(
                number=1,
                reused=False,
            ),
            DeliveryError(
                "delivery failed"
            ),
        ]
    )

    result = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                prepared_outcome(
                    "FIRST_FLAG",
                    path="first.py",
                ),
                prepared_outcome(
                    "SECOND_FLAG",
                    path="second.py",
                ),
                failed_outcome(
                    "THIRD_FLAG"
                ),
            )
        ),
        delivery=delivery,
    ).run("context")

    assert (
        len(
            result.successful_deliveries
        )
        == 1
    )

    assert (
        len(
            result.failed_deliveries
        )
        == 1
    )

    assert (
        len(
            result.not_prepared_targets
        )
        == 1
    )

    assert result.has_failures is True


def test_reused_pr_counts_as_success():
    result = RemediationPipeline(
        orchestrator=FakeOrchestrator(
            orchestration_result(
                prepared_outcome(
                    "OLD_FLAG"
                )
            )
        ),
        delivery=FakeDelivery(
            [
                pull_request(
                    reused=True
                )
            ]
        ),
    ).run("context")

    assert (
        len(
            result.successful_deliveries
        )
        == 1
    )

    assert result.has_failures is False