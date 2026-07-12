from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

import libcst as cst
from libcst.metadata import (
    MetadataWrapper,
    PositionProvider,
)


_FLAG_NAME_PATTERN = re.compile(
    r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$"
)


class CandidateExtractionError(
    RuntimeError
):
    """
    Raised when deterministic candidate extraction
    cannot safely complete.
    """


class CandidateKind(
    str,
    Enum,
):
    IF_STATEMENT = "if_statement"
    TERNARY_EXPRESSION = (
        "ternary_expression"
    )


@dataclass(
    frozen=True,
    order=True,
)
class CandidateOccurrence:
    file_path: str
    line: int
    kind: CandidateKind
    snippet: str


@dataclass(frozen=True)
class FeatureFlagCandidate:
    flag_name: str

    occurrences: tuple[
        CandidateOccurrence,
        ...,
    ]

    @property
    def files(
        self,
    ) -> frozenset[str]:
        return frozenset(
            occurrence.file_path
            for occurrence
            in self.occurrences
        )


class _DirectFlagVisitor(
    cst.CSTVisitor
):
    METADATA_DEPENDENCIES = (
        PositionProvider,
    )

    def __init__(
        self,
        *,
        file_path: str,
        source_lines: list[str],
        context_lines: int,
        max_snippet_chars: int,
    ) -> None:
        self.file_path = file_path
        self.source_lines = source_lines
        self.context_lines = (
            context_lines
        )
        self.max_snippet_chars = (
            max_snippet_chars
        )

        self.occurrences: list[
            tuple[
                str,
                CandidateOccurrence,
            ]
        ] = []

    def visit_If(
        self,
        node: cst.If,
    ) -> None:
        self._record(
            test=node.test,
            node=node,
            kind=(
                CandidateKind
                .IF_STATEMENT
            ),
        )

    def visit_IfExp(
        self,
        node: cst.IfExp,
    ) -> None:
        self._record(
            test=node.test,
            node=node,
            kind=(
                CandidateKind
                .TERNARY_EXPRESSION
            ),
        )

    def _record(
        self,
        *,
        test: cst.BaseExpression,
        node: cst.CSTNode,
        kind: CandidateKind,
    ) -> None:
        if not isinstance(
            test,
            cst.Name,
        ):
            return

        flag_name = test.value

        if not (
            _FLAG_NAME_PATTERN
            .fullmatch(flag_name)
        ):
            return

        position = self.get_metadata(
            PositionProvider,
            node,
        )

        snippet = self._build_snippet(
            start_line=(
                position.start.line
            ),
            end_line=(
                position.end.line
            ),
        )

        self.occurrences.append(
            (
                flag_name,
                CandidateOccurrence(
                    file_path=(
                        self.file_path
                    ),
                    line=(
                        position
                        .start
                        .line
                    ),
                    kind=kind,
                    snippet=snippet,
                ),
            )
        )

    def _build_snippet(
        self,
        *,
        start_line: int,
        end_line: int,
    ) -> str:
        first = max(
            1,
            start_line
            - self.context_lines,
        )

        last = min(
            len(self.source_lines),
            end_line
            + self.context_lines,
        )

        snippet = "\n".join(
            (
                f"{line_number:>4}: "
                f"{self.source_lines[line_number - 1]}"
            )
            for line_number
            in range(
                first,
                last + 1,
            )
        )

        if (
            len(snippet)
            <= self.max_snippet_chars
        ):
            return snippet

        marker = (
            "\n... [snippet truncated]"
        )

        characters_to_keep = max(
            0,
            self.max_snippet_chars
            - len(marker),
        )

        return (
            snippet[
                :characters_to_keep
            ].rstrip()
            + marker
        )


class CandidateExtractor:
    """
    Extract direct boolean-name flag candidates
    without using an LLM.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        context_lines: int = 2,
        max_file_bytes: int = 512_000,
        max_snippet_chars: int = 2_000,
        max_occurrences_per_flag: int = 50,
    ) -> None:
        self.repo_root = (
            repo_root.resolve()
        )

        self.context_lines = (
            context_lines
        )

        self.max_file_bytes = (
            max_file_bytes
        )

        self.max_snippet_chars = (
            max_snippet_chars
        )

        self.max_occurrences_per_flag = (
            max_occurrences_per_flag
        )

        if context_lines < 0:
            raise ValueError(
                "context_lines cannot "
                "be negative"
            )

        if max_file_bytes <= 0:
            raise ValueError(
                "max_file_bytes must "
                "be positive"
            )

        if max_snippet_chars <= 0:
            raise ValueError(
                "max_snippet_chars must "
                "be positive"
            )

        if (
            max_occurrences_per_flag
            <= 0
        ):
            raise ValueError(
                "max_occurrences_per_flag "
                "must be positive"
            )

    def extract(
        self,
        python_files: Iterable[Path],
    ) -> tuple[
        FeatureFlagCandidate,
        ...,
    ]:
        grouped: dict[
            str,
            list[CandidateOccurrence],
        ] = defaultdict(list)

        resolved_files = sorted(
            {
                Path(path).resolve()
                for path in python_files
            },
            key=lambda path: (
                path.as_posix()
            ),
        )

        for file_path in resolved_files:
            relative_path = (
                _repository_relative_python_path(
                    self.repo_root,
                    file_path,
                )
            )

            try:
                file_size = (
                    file_path
                    .stat()
                    .st_size
                )

            except OSError as error:
                raise (
                    CandidateExtractionError(
                        "Could not inspect "
                        f"{relative_path}: "
                        f"{error}"
                    )
                ) from error

            if (
                file_size
                > self.max_file_bytes
            ):
                raise CandidateExtractionError(
                    "Refusing to parse oversized "
                    "Python file "
                    f"{relative_path} "
                    f"({file_size} bytes; "
                    "limit is "
                    f"{self.max_file_bytes})."
                )

            try:
                source = (
                    file_path.read_text(
                        encoding="utf-8"
                    )
                )

                module = (
                    cst.parse_module(
                        source
                    )
                )

            except (
                OSError,
                UnicodeError,
                cst.ParserSyntaxError,
            ) as error:
                raise (
                    CandidateExtractionError(
                        "Could not parse "
                        f"{relative_path}: "
                        f"{error}"
                    )
                ) from error

            visitor = _DirectFlagVisitor(
                file_path=relative_path,
                source_lines=(
                    source.splitlines()
                ),
                context_lines=(
                    self.context_lines
                ),
                max_snippet_chars=(
                    self
                    .max_snippet_chars
                ),
            )

            MetadataWrapper(
                module
            ).visit(visitor)

            for (
                flag_name,
                occurrence,
            ) in visitor.occurrences:
                occurrences = grouped[
                    flag_name
                ]

                if (
                    len(occurrences)
                    >= self
                    .max_occurrences_per_flag
                ):
                    raise (
                        CandidateExtractionError(
                            f"Flag {flag_name!r} "
                            "exceeds the configured "
                            "occurrence limit of "
                            f"{self.max_occurrences_per_flag}."
                        )
                    )

                occurrences.append(
                    occurrence
                )

        return tuple(
            FeatureFlagCandidate(
                flag_name=flag_name,
                occurrences=tuple(
                    sorted(occurrences)
                ),
            )
            for (
                flag_name,
                occurrences,
            )
            in sorted(
                grouped.items()
            )
        )


def build_repository_allowlist(
    repo_root: Path,
    python_files: Iterable[Path],
) -> frozenset[str]:
    """
    Build canonical POSIX paths from
    scanner-generated Python files.
    """
    resolved_root = (
        repo_root.resolve()
    )

    return frozenset(
        _repository_relative_python_path(
            resolved_root,
            Path(path).resolve(),
        )
        for path in python_files
    )


def _repository_relative_python_path(
    repo_root: Path,
    file_path: Path,
) -> str:
    try:
        relative = (
            file_path.relative_to(
                repo_root
            )
        )

    except ValueError as error:
        raise ValueError(
            "Python file is outside "
            "repository root: "
            f"{file_path}"
        ) from error

    if not file_path.is_file():
        raise ValueError(
            "Python file does not exist: "
            f"{file_path}"
        )

    if (
        file_path.suffix.lower()
        != ".py"
    ):
        raise ValueError(
            "Only Python files are allowed: "
            f"{file_path}"
        )

    parts = relative.parts

    if (
        not parts
        or any(
            part in {
                "",
                ".",
                "..",
            }
            for part in parts
        )
    ):
        raise ValueError(
            "Unsafe repository path: "
            f"{file_path}"
        )

    return relative.as_posix()