from __future__ import annotations

from collections.abc import (
    Iterable,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from project.discovery.candidates import (
    CandidateExtractor,
    FeatureFlagCandidate,
    build_repository_allowlist,
)
from project.discovery.scanner import (
    RepoScanner,
)
from project.discovery.schema import (
    DiscoveryPlan,
)


class DiscoveryClient(Protocol):
    def discover(
        self,
        *,
        candidates: Sequence[
            FeatureFlagCandidate
        ],
        repository_allowlist: Iterable[str],
        business_context: str,
    ) -> DiscoveryPlan:
        ...


@dataclass(frozen=True)
class DiscoveryResult:
    plan: DiscoveryPlan

    candidates: tuple[
        FeatureFlagCandidate,
        ...,
    ]

    repository_allowlist: frozenset[str]


class DiscoveryService:
    """
    Independent V7 pipeline:

    1. Scan repository.
    2. Build the deterministic path allowlist.
    3. Extract direct-name flag candidates.
    4. Ask the read-only discovery client to classify them.
    5. Return the plan and its deterministic audit context.
    """

    def __init__(
        self,
        repo_root: Path,
        discovery_client: DiscoveryClient,
        *,
        scanner: RepoScanner | None = None,
        extractor: CandidateExtractor | None = None,
    ) -> None:
        self.repo_root = (
            repo_root.resolve()
        )

        self.discovery_client = (
            discovery_client
        )

        self.scanner = (
            scanner
            or RepoScanner(
                self.repo_root
            )
        )

        self.extractor = (
            extractor
            or CandidateExtractor(
                self.repo_root
            )
        )

    def discover(
        self,
        business_context: str,
    ) -> DiscoveryResult:
        # Materialize once because both the allowlist
        # and candidate extractor need the same scanner
        # output.
        python_files = tuple(
            self.scanner
            .get_python_files()
        )

        repository_allowlist = (
            build_repository_allowlist(
                self.repo_root,
                python_files,
            )
        )

        candidates = (
            self.extractor.extract(
                python_files
            )
        )

        plan = (
            self.discovery_client
            .discover(
                candidates=candidates,
                repository_allowlist=(
                    repository_allowlist
                ),
                business_context=(
                    business_context
                ),
            )
        )

        return DiscoveryResult(
            plan=plan,
            candidates=candidates,
            repository_allowlist=(
                repository_allowlist
            ),
        )