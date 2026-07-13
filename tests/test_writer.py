from __future__ import annotations

import os
from pathlib import Path

import pytest

from project.core import writer
from project.core.writer import (
    ConcurrentFileModificationError,
    EphemeralFileTransaction,
    FileTransactionError,
)


def test_context_applies_then_restores_exact_original_bytes(
    tmp_path: Path,
):
    target = tmp_path / "app.py"

    original = (
        b"\xef\xbb\xbf"
        b"OLD_FLAG = False\r\n"
    )

    replacement = (
        b"def target():\n"
        b"    return 'old'\n"
    )

    target.write_bytes(original)

    with EphemeralFileTransaction(
        tmp_path,
        {"app.py": replacement},
    ):
        assert target.read_bytes() == replacement

    assert target.read_bytes() == original


def test_context_restores_files_when_body_raises(
    tmp_path: Path,
):
    target = tmp_path / "app.py"
    target.write_bytes(b"original\n")

    with pytest.raises(
        RuntimeError,
        match="validation failed",
    ):
        with EphemeralFileTransaction(
            tmp_path,
            {"app.py": b"replacement\n"},
        ):
            raise RuntimeError(
                "validation failed"
            )

    assert target.read_bytes() == b"original\n"


def test_multiple_files_are_applied_and_restored(
    tmp_path: Path,
):
    first = tmp_path / "a.py"
    second = tmp_path / "pkg" / "b.py"

    second.parent.mkdir()

    first.write_bytes(b"a-original")
    second.write_bytes(b"b-original")

    with EphemeralFileTransaction(
        tmp_path,
        {
            "pkg/b.py": b"b-new",
            "a.py": b"a-new",
        },
    ) as transaction:
        assert (
            transaction.replacement_paths
            == (
                "a.py",
                "pkg/b.py",
            )
        )

        assert (
            first.read_bytes()
            == b"a-new"
        )

        assert (
            second.read_bytes()
            == b"b-new"
        )

    assert (
        first.read_bytes()
        == b"a-original"
    )

    assert (
        second.read_bytes()
        == b"b-original"
    )


def test_preserves_original_file_mode(
    tmp_path: Path,
):
    target = tmp_path / "app.py"

    target.write_bytes(b"original")
    os.chmod(target, 0o640)

    original_mode = target.stat().st_mode

    with EphemeralFileTransaction(
        tmp_path,
        {"app.py": b"replacement"},
    ):
        assert (
            target.stat().st_mode
            == original_mode
        )

    assert (
        target.stat().st_mode
        == original_mode
    )


def test_detects_file_change_between_snapshot_and_apply(
    tmp_path: Path,
):
    target = tmp_path / "app.py"
    target.write_bytes(b"original")

    transaction = EphemeralFileTransaction(
        tmp_path,
        {"app.py": b"replacement"},
    )

    target.write_bytes(
        b"external change"
    )

    with pytest.raises(
        ConcurrentFileModificationError,
        match=(
            "changed after it was "
            "snapshotted"
        ),
    ):
        transaction.apply()

    assert (
        target.read_bytes()
        == b"external change"
    )


def test_partial_apply_failure_restores_already_written_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"

    first.write_bytes(b"a-original")
    second.write_bytes(b"b-original")

    real_atomic_write = (
        writer._atomic_write_bytes
    )

    replacement_writes = 0

    def flaky_atomic_write(
        *,
        path: Path,
        content: bytes,
        mode: int,
    ):
        nonlocal replacement_writes

        if content in {
            b"a-new",
            b"b-new",
        }:
            replacement_writes += 1

            if replacement_writes == 2:
                raise OSError(
                    "simulated disk failure"
                )

        real_atomic_write(
            path=path,
            content=content,
            mode=mode,
        )

    monkeypatch.setattr(
        writer,
        "_atomic_write_bytes",
        flaky_atomic_write,
    )

    transaction = EphemeralFileTransaction(
        tmp_path,
        {
            "a.py": b"a-new",
            "b.py": b"b-new",
        },
    )

    with pytest.raises(
        FileTransactionError,
        match="Could not apply",
    ):
        transaction.apply()

    assert (
        first.read_bytes()
        == b"a-original"
    )

    assert (
        second.read_bytes()
        == b"b-original"
    )


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.py",
        r"C:\repo\app.py",
        "../outside.py",
        "pkg/../outside.py",
        "pkg/./app.py",
        "pkg//app.py",
        "README.md",
    ],
)
def test_rejects_unsafe_paths_before_writing(
    tmp_path: Path,
    path: str,
):
    safe = tmp_path / "safe.py"
    safe.write_bytes(b"safe")

    with pytest.raises(
        (TypeError, ValueError)
    ):
        EphemeralFileTransaction(
            tmp_path,
            {
                "safe.py": b"changed",
                path: b"bad",
            },
        )

    assert safe.read_bytes() == b"safe"


def test_rejects_non_bytes_replacement(
    tmp_path: Path,
):
    target = tmp_path / "app.py"
    target.write_bytes(b"original")

    with pytest.raises(
        TypeError,
        match="must be bytes",
    ):
        EphemeralFileTransaction(
            tmp_path,
            {
                # Intentional invalid type.
                "app.py": "replacement",
            },  # type: ignore[dict-item]
        )


def test_rejects_missing_target_before_writing_other_files(
    tmp_path: Path,
):
    existing = tmp_path / "existing.py"
    existing.write_bytes(b"original")

    with pytest.raises(
        ValueError,
        match="not a file",
    ):
        EphemeralFileTransaction(
            tmp_path,
            {
                "existing.py": b"changed",
                "missing.py": b"missing",
            },
        )

    assert (
        existing.read_bytes()
        == b"original"
    )


def test_rejects_symbolic_link_target(
    tmp_path: Path,
):
    real = tmp_path / "real.py"
    link = tmp_path / "link.py"

    real.write_bytes(b"original")

    try:
        link.symlink_to(real)
    except (
        OSError,
        NotImplementedError,
    ):
        pytest.skip(
            "Symbolic links are unavailable "
            "in this environment"
        )

    with pytest.raises(
        ValueError,
        match="Symbolic-link",
    ):
        EphemeralFileTransaction(
            tmp_path,
            {"link.py": b"replacement"},
        )

    assert real.read_bytes() == b"original"


def test_rollback_is_idempotent(
    tmp_path: Path,
):
    target = tmp_path / "app.py"
    target.write_bytes(b"original")

    transaction = EphemeralFileTransaction(
        tmp_path,
        {"app.py": b"replacement"},
    )

    transaction.apply()
    transaction.rollback()

    # A repeated cleanup call must be harmless.
    transaction.rollback()

    assert target.read_bytes() == b"original"