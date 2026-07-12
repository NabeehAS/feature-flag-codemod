from types import SimpleNamespace

import pytest

from project.discovery.candidates import (
    CandidateKind,
    CandidateOccurrence,
    FeatureFlagCandidate,
)
from project.discovery.llm import (
    DiscoveryError,
    DiscoveryValidationError,
    OpenAIFeatureFlagDiscovery,
)
from project.discovery.schema import (
    DiscoveryPlan,
    EvidenceItem,
    RemediationTarget,
)


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)

        output = self.outputs.pop(0)

        if isinstance(output, Exception):
            raise output

        return output


class FakeClient:
    def __init__(self, outputs):
        self.responses = FakeResponses(
            outputs
        )


def candidate(
    flag: str = "OLD_FLAG",
    files: tuple[str, ...] = ("app.py",),
) -> FeatureFlagCandidate:
    return FeatureFlagCandidate(
        flag_name=flag,
        occurrences=tuple(
            CandidateOccurrence(
                file_path=path,
                line=1,
                kind=(
                    CandidateKind.IF_STATEMENT
                ),
                snippet=(
                    f"   1: if {flag}:\n"
                    "   2:     pass"
                ),
            )
            for path in files
        ),
    )


def target(
    flag: str = "OLD_FLAG",
    files: tuple[str, ...] = ("app.py",),
    state: bool = False,
) -> RemediationTarget:
    return RemediationTarget(
        flag_name=flag,
        final_state=state,
        files_affected=list(files),
        evidence=[
            EvidenceItem(
                source="business_context",
                quote=(
                    "OLD_FLAG was permanently "
                    "disabled."
                ),
                explanation=(
                    "This explicitly states the "
                    "permanent final state."
                ),
            )
        ],
    )


def response(
    plan,
    status: str = "completed",
):
    return SimpleNamespace(
        status=status,
        output_parsed=plan,
    )


def discovery(
    outputs,
    **kwargs,
):
    client = FakeClient(outputs)

    service = OpenAIFeatureFlagDiscovery(
        client=client,
        **kwargs,
    )

    return service, client


def test_valid_plan_is_returned_and_call_uses_structured_output():
    service, client = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(),
                    ]
                )
            )
        ]
    )

    result = service.discover(
        candidates=[
            candidate(),
        ],
        repository_allowlist={
            "app.py",
            "other.py",
        },
        business_context=(
            "OLD_FLAG was permanently disabled."
        ),
    )

    assert (
        result.targets[0].flag_name
        == "OLD_FLAG"
    )

    call = client.responses.calls[0]

    assert (
        call["text_format"]
        is DiscoveryPlan
    )
    assert call["store"] is False
    assert call["model"] == "gpt-5-mini"
    assert (
        call["max_output_tokens"]
        == 4_000
    )

    assert (
        "OLD_FLAG"
        in call["input"][1]["content"]
    )


def test_no_candidates_returns_empty_plan_without_api_call():
    service, client = discovery([])

    result = service.discover(
        candidates=[],
        repository_allowlist=set(),
        business_context="anything",
    )

    assert result.targets == []
    assert client.responses.calls == []


def test_unknown_flag_rejects_entire_plan():
    service, _ = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(
                            flag="INVENTED_FLAG"
                        ),
                    ]
                )
            )
        ],
        max_attempts=1,
    )

    with pytest.raises(
        DiscoveryValidationError,
        match="Unknown flag",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_hallucinated_path_rejects_entire_plan():
    service, _ = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(
                            files=(
                                "outside.py",
                            )
                        ),
                    ]
                )
            )
        ],
        max_attempts=1,
    )

    with pytest.raises(
        DiscoveryValidationError,
        match=(
            "outside the repository allowlist"
        ),
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_allowlisted_but_unrelated_path_is_rejected():
    service, _ = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(
                            files=(
                                "other.py",
                            )
                        ),
                    ]
                )
            )
        ],
        max_attempts=1,
    )

    with pytest.raises(
        DiscoveryValidationError,
        match="do not contain that candidate",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
                "other.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_omitting_a_candidate_file_is_rejected():
    service, _ = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(
                            files=(
                                "app.py",
                            )
                        ),
                    ]
                )
            )
        ],
        max_attempts=1,
    )

    with pytest.raises(
        DiscoveryValidationError,
        match="omitted candidate",
    ):
        service.discover(
            candidates=[
                candidate(
                    files=(
                        "app.py",
                        "routes.py",
                    )
                )
            ],
            repository_allowlist={
                "app.py",
                "routes.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_windows_paths_are_canonicalized_after_validation():
    service, _ = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(
                            files=(
                                r"pkg\app.py",
                            )
                        ),
                    ]
                )
            )
        ]
    )

    result = service.discover(
        candidates=[
            candidate(
                files=(
                    "pkg/app.py",
                )
            )
        ],
        repository_allowlist={
            "pkg/app.py",
        },
        business_context=(
            "OLD_FLAG was permanently disabled."
        ),
    )

    assert (
        result.targets[0].files_affected
        == ["pkg/app.py"]
    )


def test_semantic_failure_retries_with_correction_message():
    invalid = response(
        DiscoveryPlan(
            targets=[
                target(
                    flag="INVENTED_FLAG",
                ),
            ]
        )
    )

    valid = response(
        DiscoveryPlan(
            targets=[
                target(),
            ]
        )
    )

    service, client = discovery(
        [
            invalid,
            valid,
        ],
        max_attempts=2,
    )

    result = service.discover(
        candidates=[
            candidate(),
        ],
        repository_allowlist={
            "app.py",
        },
        business_context=(
            "OLD_FLAG was permanently disabled."
        ),
    )

    assert (
        result.targets[0].flag_name
        == "OLD_FLAG"
    )
    assert len(
        client.responses.calls
    ) == 2

    second_prompt = (
        client.responses.calls[1]
        ["input"][1]["content"]
    )

    assert (
        "PREVIOUS OUTPUT WAS REJECTED"
        in second_prompt
    )


def test_exhausted_retries_raise_validation_error():
    invalid = response(
        DiscoveryPlan(
            targets=[
                target(
                    flag="INVENTED_FLAG",
                ),
            ]
        )
    )

    service, _ = discovery(
        [
            invalid,
            invalid,
        ],
        max_attempts=2,
    )

    with pytest.raises(
        DiscoveryValidationError,
        match="after 2 attempt",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_missing_parsed_output_fails_without_semantic_retry():
    service, _ = discovery(
        [
            response(None),
        ]
    )

    with pytest.raises(
        DiscoveryError,
        match="no parsed discovery plan",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_incomplete_response_fails():
    service, _ = discovery(
        [
            response(
                None,
                status="incomplete",
            )
        ]
    )

    with pytest.raises(
        DiscoveryError,
        match="did not complete",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )


def test_candidate_outside_allowlist_rejected_before_api_call():
    service, client = discovery([])

    with pytest.raises(
        ValueError,
        match="outside the repository allowlist",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "other.py",
            },
            business_context=(
                "OLD_FLAG was permanently "
                "disabled."
            ),
        )

    assert client.responses.calls == []


def test_ungrounded_business_evidence_is_rejected():
    service, _ = discovery(
        [
            response(
                DiscoveryPlan(
                    targets=[
                        target(),
                    ]
                )
            )
        ],
        max_attempts=1,
    )

    with pytest.raises(
        DiscoveryValidationError,
        match="ungrounded",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context=(
                "A different statement with "
                "no matching quote."
            ),
        )


def test_empty_business_context_returns_empty_plan_without_api_call():
    service, client = discovery([])

    result = service.discover(
        candidates=[
            candidate(),
        ],
        repository_allowlist={
            "app.py",
        },
        business_context="   ",
    )

    assert result.targets == []
    assert client.responses.calls == []


def test_business_context_size_limit():
    service, client = discovery(
        [],
        max_business_context_chars=4,
    )

    with pytest.raises(
        ValueError,
        match="business_context",
    ):
        service.discover(
            candidates=[
                candidate(),
            ],
            repository_allowlist={
                "app.py",
            },
            business_context="too long",
        )

    assert client.responses.calls == []