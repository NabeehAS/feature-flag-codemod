from pathlib import Path

import pytest

from project.discovery.candidates import (
    CandidateExtractionError,
    CandidateExtractor,
    CandidateKind,
    build_repository_allowlist,
)


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        content,
        encoding="utf-8",
    )
    return path


def test_extracts_if_and_ternary_and_groups_by_flag(
    tmp_path: Path,
):
    first = write(
        tmp_path / "app.py",
        (
            "if OLD_FLAG:\n"
            "    run_new()\n"
            "else:\n"
            "    run_old()\n"
        ),
    )

    second = write(
        tmp_path / "routes.py",
        (
            "handler = new_handler "
            "if OLD_FLAG else old_handler\n"
        ),
    )

    candidates = CandidateExtractor(
        tmp_path
    ).extract(
        [
            second,
            first,
        ]
    )

    assert len(candidates) == 1

    candidate = candidates[0]

    assert candidate.flag_name == "OLD_FLAG"

    assert candidate.files == frozenset(
        {
            "app.py",
            "routes.py",
        }
    )

    assert [
        occurrence.kind
        for occurrence in candidate.occurrences
    ] == [
        CandidateKind.IF_STATEMENT,
        CandidateKind.TERNARY_EXPRESSION,
    ]

    assert (
        "1: if OLD_FLAG:"
        in candidate.occurrences[0].snippet
    )


def test_ignores_lowercase_and_non_direct_expressions(
    tmp_path: Path,
):
    file_path = write(
        tmp_path / "app.py",
        (
            "if enabled:\n"
            "    pass\n"
            "\n"
            "if flags.is_enabled('x'):\n"
            "    pass\n"
        ),
    )

    assert (
        CandidateExtractor(
            tmp_path
        ).extract([file_path])
        == ()
    )


def test_accepts_uppercase_snake_candidate(
    tmp_path: Path,
):
    file_path = write(
        tmp_path / "app.py",
        (
            "if ENABLE_NEW_UI:\n"
            "    pass\n"
        ),
    )

    candidates = CandidateExtractor(
        tmp_path
    ).extract([file_path])

    assert (
        candidates[0].flag_name
        == "ENABLE_NEW_UI"
    )


def test_builds_posix_allowlist(
    tmp_path: Path,
):
    file_path = write(
        tmp_path / "pkg" / "app.py",
        "pass\n",
    )

    allowlist = build_repository_allowlist(
        tmp_path,
        [file_path],
    )

    assert allowlist == frozenset(
        {
            "pkg/app.py",
        }
    )


def test_rejects_file_outside_repo(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()

    outside = write(
        tmp_path / "outside.py",
        "pass\n",
    )

    with pytest.raises(
        ValueError,
        match="outside repository",
    ):
        build_repository_allowlist(
            repo,
            [outside],
        )


def test_parse_failure_is_not_silently_skipped(
    tmp_path: Path,
):
    file_path = write(
        tmp_path / "bad.py",
        "if:\n",
    )

    with pytest.raises(
        CandidateExtractionError,
        match="Could not parse",
    ):
        CandidateExtractor(
            tmp_path
        ).extract([file_path])


def test_oversized_file_is_rejected(
    tmp_path: Path,
):
    file_path = write(
        tmp_path / "large.py",
        (
            "if OLD_FLAG:\n"
            "    pass\n"
        ),
    )

    with pytest.raises(
        CandidateExtractionError,
        match="oversized",
    ):
        CandidateExtractor(
            tmp_path,
            max_file_bytes=4,
        ).extract([file_path])


def test_occurrence_limit_is_enforced(
    tmp_path: Path,
):
    file_path = write(
        tmp_path / "app.py",
        (
            "if OLD_FLAG:\n"
            "    pass\n"
            "if OLD_FLAG:\n"
            "    pass\n"
        ),
    )

    with pytest.raises(
        CandidateExtractionError,
        match="occurrence limit",
    ):
        CandidateExtractor(
            tmp_path,
            max_occurrences_per_flag=1,
        ).extract([file_path])