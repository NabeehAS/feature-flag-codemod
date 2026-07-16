from __future__ import annotations

import codecs
from pathlib import Path

import pytest
 
from project.discovery.schema import (
    DiscoveryPlan,
    EvidenceItem,
    RemediationTarget,
)
from project.discovery.service import DiscoveryResult
from project.orchestrator import (
    BaselineValidationError,
    OrchestrationError,
    RemediationOrchestrator,
    TargetStatus,
)
from project.validation.runner import ValidationResult


class FakeDiscoveryService:
    def __init__(self, result: DiscoveryResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def discover(
        self,
        business_context: str,
    ) -> DiscoveryResult:
        self.calls.append(business_context)
        return self.result


class SequencedValidator:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> ValidationResult:
        self.calls += 1
        return self.results.pop(0)


def passed(
    output: str = "passed",
) -> ValidationResult:
    return ValidationResult(
        passed=True,
        output=output,
        returncode=0,
    )


def failed(
    output: str = "failed",
) -> ValidationResult:
    return ValidationResult(
        passed=False,
        output=output,
        returncode=1,
    )


def target(
    flag_name: str,
    final_state: bool,
    files: list[str],
) -> RemediationTarget:
    return RemediationTarget(
        flag_name=flag_name,
        final_state=final_state,
        files_affected=files,
        evidence=[
            EvidenceItem(
                source="business_context",
                quote=(
                    f"{flag_name} is permanent."
                ),
                explanation=(
                    "Explicit final-state evidence."
                ),
            )
        ],
    )


def discovery_result(
    *targets: RemediationTarget,
    allowlist: set[str] | None = None,
) -> DiscoveryResult:
    if allowlist is None:
        allowlist = {
            path
            for item in targets
            for path in item.files_affected
        }

    return DiscoveryResult(
        plan=DiscoveryPlan(
            targets=list(targets),
        ),
        candidates=(),
        repository_allowlist=frozenset(
            allowlist
        ),
    )


def test_no_targets_skips_validation(
    tmp_path: Path,
):
    service = FakeDiscoveryService(
        discovery_result()
    )

    validator = SequencedValidator([])

    result = RemediationOrchestrator(
        tmp_path,
        service,
        validator,
    ).run("context")

    assert service.calls == ["context"]
    assert validator.calls == 0
    assert result.baseline_validation is None
    assert result.outcomes == ()


def test_baseline_failure_aborts_before_mutation(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    original = (
        b"if OLD_FLAG:\n"
        b"    run_new()\n"
        b"else:\n"
        b"    run_old()\n"
    )

    app.write_bytes(original)

    service = FakeDiscoveryService(
        discovery_result(
            target(
                "OLD_FLAG",
                False,
                ["app.py"],
            )
        )
    )

    validator = SequencedValidator(
        [
            failed("baseline broken"),
        ]
    )

    with pytest.raises(
        BaselineValidationError
    ) as exc_info:
        RemediationOrchestrator(
            tmp_path,
            service,
            validator,
        ).run(
            "OLD_FLAG is permanent."
        )

    assert (
        exc_info.value.result.output
        == "baseline broken"
    )

    assert validator.calls == 1
    assert app.read_bytes() == original


def test_successful_target_is_validated_and_local_file_is_restored(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    original = (
        "if OLD_FLAG:\n"
        "    run_new()\n"
        "else:\n"
        "    run_old()\n"
    ).encode()

    app.write_bytes(original)

    calls = 0

    def validate() -> ValidationResult:
        nonlocal calls
        calls += 1

        if calls == 1:
            assert (
                app.read_bytes()
                == original
            )
        else:
            assert (
                app.read_text(
                    encoding="utf-8"
                )
                == "run_old()\n"
            )

        return passed()

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    ["app.py"],
                )
            )
        ),
        validate,
    ).run(
        "OLD_FLAG is permanent."
    )

    assert calls == 2
    assert app.read_bytes() == original

    outcome = result.outcomes[0]

    assert (
        outcome.status
        is TargetStatus.PREPARED
    )

    assert outcome.validation is not None
    assert outcome.validation.passed

    assert outcome.changed_files == {
        "app.py": "run_old()\n",
    }

    assert (
        len(
            outcome.files[0]
            .original_sha256
        )
        == 64
    )

    assert (
        len(
            outcome.files[0]
            .updated_sha256
        )
        == 64
    )


def test_failed_post_validation_restores_and_returns_failure(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    original = (
        b"if OLD_FLAG:\n"
        b"    new()\n"
        b"else:\n"
        b"    old()\n"
    )

    app.write_bytes(original)

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    ["app.py"],
                )
            )
        ),
        SequencedValidator(
            [
                passed(),
                failed("regression"),
            ]
        ),
    ).run(
        "OLD_FLAG is permanent."
    )

    assert app.read_bytes() == original

    outcome = result.outcomes[0]

    assert (
        outcome.status
        is TargetStatus.VALIDATION_FAILED
    )

    assert outcome.files == ()
    assert outcome.validation is not None

    assert (
        outcome.validation.output
        == "regression"
    )


def test_multi_file_target_is_applied_as_one_validation_unit(
    tmp_path: Path,
):
    first = tmp_path / "app.py"
    second = tmp_path / "routes.py"

    first_original = (
        b"if OLD_FLAG:\n"
        b"    new_app()\n"
        b"else:\n"
        b"    old_app()\n"
    )

    second_original = (
        b"handler = new "
        b"if OLD_FLAG else old\n"
    )

    first.write_bytes(first_original)
    second.write_bytes(second_original)

    calls = 0

    def validate() -> ValidationResult:
        nonlocal calls
        calls += 1

        if calls == 2:
            assert (
                first.read_text(
                    encoding="utf-8"
                )
                == "old_app()\n"
            )

            assert (
                second.read_text(
                    encoding="utf-8"
                )
                == "handler = old\n"
            )

        return passed()

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    [
                        "app.py",
                        "routes.py",
                    ],
                )
            )
        ),
        validate,
    ).run(
        "OLD_FLAG is permanent."
    )

    assert calls == 2

    assert (
        first.read_bytes()
        == first_original
    )

    assert (
        second.read_bytes()
        == second_original
    )

    assert (
        result.outcomes[0]
        .changed_files
        == {
            "app.py": "old_app()\n",
            "routes.py": (
                "handler = old\n"
            ),
        }
    )


def test_import_cleanup_is_part_of_prepared_source(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    app.write_text(
        (
            "from service import "
            "new_handler, old_handler\n\n"
            "if OLD_FLAG:\n"
            "    handler = new_handler\n"
            "else:\n"
            "    handler = old_handler\n"
        ),
        encoding="utf-8",
    )

    original = app.read_bytes()

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    ["app.py"],
                )
            )
        ),
        SequencedValidator(
            [
                passed(),
                passed(),
            ]
        ),
    ).run(
        "OLD_FLAG is permanent."
    )

    assert (
        result.outcomes[0]
        .changed_files
        == {
            "app.py": (
                "from service import "
                "old_handler\n"
                "handler = old_handler\n"
            )
        }
    )

    assert app.read_bytes() == original


def test_one_unchanged_file_rejects_entire_target_without_post_validation(
    tmp_path: Path,
):
    changed = tmp_path / "changed.py"
    unchanged = tmp_path / "unchanged.py"

    changed_original = (
        b"if OLD_FLAG:\n"
        b"    new()\n"
        b"else:\n"
        b"    old()\n"
    )

    unchanged_original = (
        b"print('no flag')\n"
    )

    changed.write_bytes(
        changed_original
    )

    unchanged.write_bytes(
        unchanged_original
    )

    validator = SequencedValidator(
        [
            passed(),
        ]
    )

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    [
                        "changed.py",
                        "unchanged.py",
                    ],
                )
            )
        ),
        validator,
    ).run(
        "OLD_FLAG is permanent."
    )

    assert validator.calls == 1

    assert (
        result.outcomes[0].status
        is TargetStatus.NO_CHANGES
    )

    assert (
        "unchanged.py"
        in result.outcomes[0].error
    )

    assert (
        changed.read_bytes()
        == changed_original
    )

    assert (
        unchanged.read_bytes()
        == unchanged_original
    )


def test_paths_outside_discovery_allowlist_are_preparation_failures(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    app.write_text(
        (
            "if OLD_FLAG:\n"
            "    pass\n"
        ),
        encoding="utf-8",
    )

    original = app.read_bytes()

    validator = SequencedValidator(
        [
            passed(),
        ]
    )

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    True,
                    ["app.py"],
                ),
                allowlist={
                    "other.py",
                },
            )
        ),
        validator,
    ).run(
        "OLD_FLAG is permanent."
    )

    assert validator.calls == 1

    assert (
        result.outcomes[0].status
        is TargetStatus.PREPARATION_FAILED
    )

    assert (
        "outside the repository allowlist"
        in result.outcomes[0].error
    )

    assert app.read_bytes() == original


def test_invalid_utf8_is_a_preparation_failure(
    tmp_path: Path,
):
    app = tmp_path / "app.py"
    original = b"\xff\xfe\x00"

    app.write_bytes(original)

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    ["app.py"],
                )
            )
        ),
        SequencedValidator(
            [
                passed(),
            ]
        ),
    ).run(
        "OLD_FLAG is permanent."
    )

    assert (
        result.outcomes[0].status
        is TargetStatus.PREPARATION_FAILED
    )

    assert (
        "not valid UTF-8"
        in result.outcomes[0].error
    )

    assert app.read_bytes() == original


def test_utf8_bom_is_preserved_in_prepared_delivery_and_local_restore(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    original = codecs.BOM_UTF8 + (
        b"if OLD_FLAG:\r\n"
        b"    new()\r\n"
        b"else:\r\n"
        b"    old()\r\n"
    )

    app.write_bytes(original)

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "OLD_FLAG",
                    False,
                    ["app.py"],
                )
            )
        ),
        SequencedValidator(
            [
                passed(),
                passed(),
            ]
        ),
    ).run(
        "OLD_FLAG is permanent."
    )

    change = result.outcomes[0].files[0]

    assert (
        change.updated_bytes.startswith(
            codecs.BOM_UTF8
        )
    )

    assert (
        change.updated_source.startswith(
            "\ufeff"
        )
    )

    assert (
        b"\r\n"
        in change.updated_bytes
    )

    assert app.read_bytes() == original


def test_multiple_targets_are_independent_and_not_accumulated(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    original = (
        "if FIRST_FLAG:\n"
        "    first_new()\n"
        "else:\n"
        "    first_old()\n\n"
        "if SECOND_FLAG:\n"
        "    second_new()\n"
        "else:\n"
        "    second_old()\n"
    ).encode()

    app.write_bytes(original)

    calls = 0

    def validate() -> ValidationResult:
        nonlocal calls
        calls += 1

        text = app.read_text(
            encoding="utf-8"
        )

        if calls == 2:
            assert "first_old()" in text
            assert "if SECOND_FLAG:" in text

            assert (
                "if FIRST_FLAG:"
                not in text
            )

        elif calls == 3:
            assert "second_new()" in text
            assert "if FIRST_FLAG:" in text

            assert (
                "if SECOND_FLAG:"
                not in text
            )

        return passed()

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "FIRST_FLAG",
                    False,
                    ["app.py"],
                ),
                target(
                    "SECOND_FLAG",
                    True,
                    ["app.py"],
                ),
            )
        ),
        validate,
    ).run(
        "flags are permanent"
    )

    assert calls == 3
    assert app.read_bytes() == original

    assert [
        item.status
        for item in result.outcomes
    ] == [
        TargetStatus.PREPARED,
        TargetStatus.PREPARED,
    ]

    assert (
        "if SECOND_FLAG:"
        in result.outcomes[0]
        .changed_files["app.py"]
    )

    assert (
        "if FIRST_FLAG:"
        in result.outcomes[1]
        .changed_files["app.py"]
    )


def test_validation_exception_is_fatal_but_transaction_restores(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    original = (
        b"if OLD_FLAG:\n"
        b"    new()\n"
        b"else:\n"
        b"    old()\n"
    )

    app.write_bytes(original)

    calls = 0

    def validate() -> ValidationResult:
        nonlocal calls
        calls += 1

        if calls == 1:
            return passed()

        raise RuntimeError(
            "validator exploded"
        )

    with pytest.raises(
        OrchestrationError,
        match="validator exploded",
    ):
        RemediationOrchestrator(
            tmp_path,
            FakeDiscoveryService(
                discovery_result(
                    target(
                        "OLD_FLAG",
                        False,
                        ["app.py"],
                    )
                )
            ),
            validate,
        ).run(
            "OLD_FLAG is permanent."
        )

    assert app.read_bytes() == original


def test_invalid_validation_return_is_fatal(
    tmp_path: Path,
):
    app = tmp_path / "app.py"

    app.write_text(
        (
            "if OLD_FLAG:\n"
            "    pass\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        OrchestrationError,
        match="invalid result",
    ):
        RemediationOrchestrator(
            tmp_path,
            FakeDiscoveryService(
                discovery_result(
                    target(
                        "OLD_FLAG",
                        True,
                        ["app.py"],
                    )
                )
            ),
            lambda: "not a result",
        ).run(
            "OLD_FLAG is permanent."
        )


def test_result_properties_separate_prepared_and_failed_targets(
    tmp_path: Path,
):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"

    first.write_text(
        (
            "if FIRST_FLAG:\n"
            "    pass\n"
        ),
        encoding="utf-8",
    )

    second.write_text(
        "print('no second flag')\n",
        encoding="utf-8",
    )

    result = RemediationOrchestrator(
        tmp_path,
        FakeDiscoveryService(
            discovery_result(
                target(
                    "FIRST_FLAG",
                    True,
                    ["first.py"],
                ),
                target(
                    "SECOND_FLAG",
                    True,
                    ["second.py"],
                ),
            )
        ),
        SequencedValidator(
            [
                passed(),
                passed(),
            ]
        ),
    ).run(
        "flags are permanent"
    )

    assert (
        len(result.prepared_targets)
        == 1
    )

    assert (
        result.prepared_targets[0]
        .target
        .flag_name
        == "FIRST_FLAG"
    )

    assert (
        len(result.failed_targets)
        == 1
    )

    assert (
        result.failed_targets[0].status
        is TargetStatus.NO_CHANGES
    )