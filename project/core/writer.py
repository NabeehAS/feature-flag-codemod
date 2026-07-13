from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType


class FileTransactionError(RuntimeError):
    """
    Base exception for ephemeral filesystem
    transaction failures.
    """


class ConcurrentFileModificationError(
    FileTransactionError
):
    """
    Raised when a snapshotted file changes before
    replacements are applied.
    """


@dataclass(frozen=True)
class FileSnapshot:
    """
    Exact original state needed to restore one
    repository file.
    """

    relative_path: str
    absolute_path: Path
    content: bytes
    mode: int


class EphemeralFileTransaction:
    """
    Temporarily replace repository files and restore
    their exact original bytes.

    All paths and replacement values are validated,
    and all original files are snapshotted before the
    first write occurs.

    Entering the context applies every replacement
    atomically.

    Exiting the context always restores the snapshots,
    whether the enclosed operation succeeds or raises.
    """

    def __init__(
        self,
        repo_root: Path,
        replacements: Mapping[str, bytes],
    ) -> None:
        self.repo_root = repo_root.resolve()

        self._replacements = (
            self._normalize_replacements(
                replacements
            )
        )

        self._snapshots = (
            self._capture_snapshots()
        )

        self._applied = False
        self._closed = False

    @property
    def snapshots(
        self,
    ) -> tuple[FileSnapshot, ...]:
        return self._snapshots

    @property
    def replacement_paths(
        self,
    ) -> tuple[str, ...]:
        return tuple(self._replacements)

    def __enter__(
        self,
    ) -> "EphemeralFileTransaction":
        self.apply()
        return self

    def __exit__(
        self,
        exc_type: (
            type[BaseException] | None
        ),
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.rollback()

        # Never suppress exceptions from validation,
        # delivery, or the calling code.
        return False

    def apply(self) -> None:
        if self._closed:
            raise FileTransactionError(
                "The file transaction is "
                "already closed."
            )

        if self._applied:
            raise FileTransactionError(
                "The file transaction has "
                "already been applied."
            )

        self._verify_snapshots_unchanged()

        written: list[
            FileSnapshot
        ] = []

        try:
            for snapshot in self._snapshots:
                _atomic_write_bytes(
                    path=(
                        snapshot.absolute_path
                    ),
                    content=(
                        self._replacements[
                            snapshot.relative_path
                        ]
                    ),
                    mode=snapshot.mode,
                )

                written.append(snapshot)

        except Exception as error:
            rollback_errors = (
                self._restore_snapshots(
                    reversed(written)
                )
            )

            message = (
                "Could not apply the "
                "file transaction."
            )

            if rollback_errors:
                message += (
                    " Rollback also failed: "
                    + "; ".join(
                        rollback_errors
                    )
                )

            self._closed = True

            raise FileTransactionError(
                message
            ) from error

        self._applied = True

    def rollback(self) -> None:
        if self._closed:
            return

        if not self._applied:
            self._closed = True
            return

        rollback_errors = (
            self._restore_snapshots(
                reversed(
                    self._snapshots
                )
            )
        )

        self._closed = True
        self._applied = False

        if rollback_errors:
            raise FileTransactionError(
                "Could not completely restore "
                "the file transaction: "
                + "; ".join(
                    rollback_errors
                )
            )

    def _normalize_replacements(
        self,
        replacements: Mapping[str, bytes],
    ) -> dict[str, bytes]:
        if not self.repo_root.is_dir():
            raise ValueError(
                "Repository root is not "
                "a directory: "
                f"{self.repo_root}"
            )

        if not isinstance(
            replacements,
            Mapping,
        ):
            raise TypeError(
                "replacements must be a mapping "
                "of paths to bytes."
            )

        if not replacements:
            raise ValueError(
                "replacements cannot be empty."
            )

        normalized: dict[
            str,
            bytes,
        ] = {}

        for (
            raw_path,
            content,
        ) in replacements.items():
            if not isinstance(
                raw_path,
                str,
            ):
                raise TypeError(
                    "Every replacement path "
                    "must be a string."
                )

            if not isinstance(
                content,
                bytes,
            ):
                raise TypeError(
                    "Replacement content for "
                    f"{raw_path!r} must be bytes."
                )

            path = _normalize_repo_path(
                raw_path
            )

            if path in normalized:
                raise ValueError(
                    "Duplicate normalized "
                    "replacement path: "
                    f"{path}"
                )

            normalized[path] = content

        # Stable ordering makes writes and tests
        # deterministic.
        return dict(
            sorted(
                normalized.items()
            )
        )

    def _capture_snapshots(
        self,
    ) -> tuple[FileSnapshot, ...]:
        snapshots: list[
            FileSnapshot
        ] = []

        for relative_path in self._replacements:
            relative_parts = (
                PurePosixPath(
                    relative_path
                ).parts
            )

            candidate_path = (
                self.repo_root.joinpath(
                    *relative_parts
                )
            )

            # Reject both file symlinks and symlinked
            # parent directories.
            current = self.repo_root

            for part in relative_parts:
                current = current / part

                if current.is_symlink():
                    raise ValueError(
                        "Symbolic-link paths "
                        "are not supported: "
                        f"{relative_path}"
                    )

            absolute_path = (
                candidate_path.resolve()
            )

            try:
                absolute_path.relative_to(
                    self.repo_root
                )

            except ValueError as error:
                raise ValueError(
                    "Replacement path resolves "
                    "outside repository root: "
                    f"{relative_path}"
                ) from error

            if not absolute_path.is_file():
                raise ValueError(
                    "Replacement target is "
                    "not a file: "
                    f"{relative_path}"
                )

            stat_result = (
                absolute_path.stat()
            )

            snapshots.append(
                FileSnapshot(
                    relative_path=(
                        relative_path
                    ),
                    absolute_path=(
                        absolute_path
                    ),
                    content=(
                        absolute_path
                        .read_bytes()
                    ),
                    mode=stat.S_IMODE(
                        stat_result.st_mode
                    ),
                )
            )

        return tuple(snapshots)

    def _verify_snapshots_unchanged(
        self,
    ) -> None:
        for snapshot in self._snapshots:
            try:
                current = (
                    snapshot
                    .absolute_path
                    .read_bytes()
                )

            except OSError as error:
                raise (
                    ConcurrentFileModificationError(
                        "Could not re-read "
                        f"{snapshot.relative_path} "
                        "before applying changes."
                    )
                ) from error

            if current != snapshot.content:
                raise (
                    ConcurrentFileModificationError(
                        "File changed after it "
                        "was snapshotted: "
                        f"{snapshot.relative_path}"
                    )
                )

    def _restore_snapshots(
        self,
        snapshots,
    ) -> list[str]:
        errors: list[str] = []

        for snapshot in snapshots:
            try:
                _atomic_write_bytes(
                    path=(
                        snapshot.absolute_path
                    ),
                    content=(
                        snapshot.content
                    ),
                    mode=snapshot.mode,
                )

            except Exception as error:
                # Aggregate restoration errors so one
                # failure does not prevent attempts to
                # restore the remaining files.
                errors.append(
                    f"{snapshot.relative_path}: "
                    f"{error}"
                )

        return errors


def _normalize_repo_path(
    raw_path: str,
) -> str:
    normalized = (
        raw_path
        .strip()
        .replace("\\", "/")
    )

    if not normalized:
        raise ValueError(
            "Replacement paths cannot be empty."
        )

    if "\x00" in normalized:
        raise ValueError(
            "Replacement paths cannot contain "
            "null bytes."
        )

    if (
        normalized.startswith("/")
        or re.match(
            r"^[A-Za-z]:",
            normalized,
        )
    ):
        raise ValueError(
            "Replacement path must be "
            "repository-relative: "
            f"{raw_path}"
        )

    parts = normalized.split("/")

    if any(
        part in {
            "",
            ".",
            "..",
        }
        for part in parts
    ):
        raise ValueError(
            "Unsafe replacement path: "
            f"{raw_path}"
        )

    path = "/".join(parts)

    if (
        PurePosixPath(path)
        .suffix
        .lower()
        != ".py"
    ):
        raise ValueError(
            "Only Python files can "
            "be replaced: "
            f"{raw_path}"
        )

    return path


def _atomic_write_bytes(
    *,
    path: Path,
    content: bytes,
    mode: int,
) -> None:
    """
    Write bytes to a temporary file in the target
    directory, flush them to disk, and atomically
    replace the destination.
    """
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()

            os.fsync(
                temporary_file.fileno()
            )

            temporary_path = Path(
                temporary_file.name
            )

        os.chmod(
            temporary_path,
            mode,
        )

        os.replace(
            temporary_path,
            path,
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