import pytest
from pydantic import ValidationError

from project.discovery.schema import (
    DiscoveryPlan,
    EvidenceItem,
    RemediationTarget,
)


def evidence(
    source: str = "business_context",
    quote: str = "Rollout reached 100%.",
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        quote=quote,
        explanation="This proves the final state.",
    )


def test_valid_plan():
    plan = DiscoveryPlan(
        targets=[
            RemediationTarget(
                flag_name="OLD_FLAG",
                final_state=True,
                files_affected=["app.py"],
                evidence=[evidence()],
            )
        ]
    )

    assert plan.targets[0].final_state is True


def test_rejects_string_boolean():
    with pytest.raises(ValidationError):
        RemediationTarget(
            flag_name="OLD_FLAG",
            final_state="true",
            files_affected=["app.py"],
            evidence=[evidence()],
        )


def test_rejects_extra_fields():
    with pytest.raises(ValidationError):
        EvidenceItem(
            source="repository",
            quote="TODO",
            explanation="A note.",
            invented=True,
        )


def test_rejects_target_without_business_context_evidence():
    with pytest.raises(
        ValidationError,
        match="business_context",
    ):
        RemediationTarget(
            flag_name="OLD_FLAG",
            final_state=False,
            files_affected=["app.py"],
            evidence=[
                evidence(
                    source="repository",
                    quote="Code contains the flag.",
                )
            ],
        )


def test_rejects_duplicate_files():
    with pytest.raises(
        ValidationError,
        match="duplicates",
    ):
        RemediationTarget(
            flag_name="OLD_FLAG",
            final_state=False,
            files_affected=[
                "app.py",
                "app.py",
            ],
            evidence=[evidence()],
        )


def test_rejects_duplicate_target_flags():
    target = RemediationTarget(
        flag_name="OLD_FLAG",
        final_state=False,
        files_affected=["app.py"],
        evidence=[evidence()],
    )

    with pytest.raises(
        ValidationError,
        match="duplicate flag",
    ):
        DiscoveryPlan(
            targets=[
                target,
                target,
            ]
        )


def test_empty_targets_is_valid():
    assert DiscoveryPlan(targets=[]).targets == []