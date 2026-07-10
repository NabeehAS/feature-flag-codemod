from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from github import (
    Auth,
    Github,
    GithubException,
    InputGitTreeElement,
)


class DeliveryError(RuntimeError):
    """Base exception for GitHub delivery failures."""


class DeliveryConflictError(DeliveryError):
    """
    Raised when delivery cannot continue without
    overwriting or conflicting with existing remote state.
    """


@dataclass(frozen=True)
class PullRequestResult:
    """Details of a created or reused pull request."""

    url: str
    branch_name: str
    commit_sha: str
    pr_number: int | None
    reused_existing_pr: bool


class GitOpsDelivery:
    """
    Deliver validated Python changes through GitHub's
    Git Database API.

    This class does not:

    - execute local git commands;
    - force-update existing branches;
    - merge pull requests;
    - retry the full multi-write transaction.

    The caller must validate the transformed repository
    before invoking this delivery layer.
    """

    BRANCH_PREFIX = "remediation/remove-"
    MAX_FLAG_SLUG_LENGTH = 80

    def __init__(
        self,
        token: str | None = None,
        repo_name: str | None = None,
        *,
        repo: Any | None = None,
        request_timeout: int = 30,
    ) -> None:
        """
        Initialize the GitHub delivery layer.

        Args:
            token:
                Fine-grained GitHub token.

                Required unless a repository object is
                injected for testing.

            repo_name:
                Repository name in ``owner/repository``
                form.

                Required unless a repository object is
                injected for testing.

            repo:
                Optional injected repository object used
                by unit tests.

            request_timeout:
                Timeout for individual PyGithub requests.
        """
        self._github: Github | None = None

        if repo is not None:
            self.repo = repo
            return

        if token is None or not token.strip():
            raise ValueError(
                "A GitHub token is required when "
                "repo is not injected."
            )

        if (
            repo_name is None
            or not repo_name.strip()
        ):
            raise ValueError(
                "repo_name is required when "
                "repo is not injected."
            )

        self._github = Github(
            auth=Auth.Token(token.strip()),
            timeout=request_timeout,
        )

        self.repo = self._github.get_repo(
            repo_name.strip()
        )

    def create_remediation_pr(
        self,
        changed_files: Mapping[str, str],
        flag_name: str,
        base_branch: str | None = None,
    ) -> PullRequestResult:
        """
        Create or safely reuse a remediation pull request.

        Args:
            changed_files:
                Mapping of repository-relative Python
                paths to complete replacement source.

            flag_name:
                Name of the deprecated feature flag.

            base_branch:
                Optional target branch.

                The repository's default branch is used
                when this argument is omitted.

        Returns:
            PullRequestResult describing the created or
            reused pull request.

        Raises:
            TypeError:
                An input has an invalid type.

            ValueError:
                An input value or path is invalid.

            DeliveryConflictError:
                Delivery cannot continue safely because
                conflicting remote state already exists.

            GithubException:
                GitHub returned another API failure.
        """
        normalized_files = (
            self._normalize_changed_files(
                changed_files
            )
        )

        normalized_flag = (
            self._validate_flag_name(
                flag_name
            )
        )

        resolved_base_branch = (
            self._resolve_base_branch(
                base_branch
            )
        )

        branch_name = self.build_branch_name(
            normalized_flag
        )

        # Idempotency check before any remote write.
        existing_pr = self._find_open_pr(
            branch_name=branch_name,
            base_branch=resolved_base_branch,
        )

        if existing_pr is not None:
            return self._result_from_existing_pr(
                existing_pr,
                branch_name,
            )

        # Do not overwrite an existing branch whose PR
        # was closed or which may belong to another run.
        if self._branch_exists(branch_name):
            raise DeliveryConflictError(
                f"Branch {branch_name!r} already exists "
                "but has no open pull request. "
                "Refusing to overwrite it."
            )

        base_ref = self.repo.get_git_ref(
            f"heads/{resolved_base_branch}"
        )

        base_commit = self.repo.get_git_commit(
            base_ref.object.sha
        )

        base_tree = self.repo.get_git_tree(
            base_commit.tree.sha
        )

        tree_elements = (
            self._create_tree_elements(
                normalized_files
            )
        )

        new_tree = self.repo.create_git_tree(
            tree=tree_elements,
            base_tree=base_tree,
        )

        new_commit = self.repo.create_git_commit(
            message=(
                "chore: remove deprecated "
                "feature flag "
                f"{normalized_flag}"
            ),
            tree=new_tree,
            parents=[base_commit],
        )

        try:
            # Create the branch directly at the final
            # commit. There is no force update.
            self.repo.create_git_ref(
                ref=(
                    "refs/heads/"
                    f"{branch_name}"
                ),
                sha=new_commit.sha,
            )

        except GithubException as error:
            if error.status != 422:
                raise

            # Another process may have created the same
            # branch and PR after our initial checks.
            raced_pr = self._find_open_pr(
                branch_name=branch_name,
                base_branch=resolved_base_branch,
            )

            if raced_pr is not None:
                return (
                    self._result_from_existing_pr(
                        raced_pr,
                        branch_name,
                    )
                )

            raise DeliveryConflictError(
                "GitHub rejected creation of "
                f"branch {branch_name!r}, and no "
                "reusable open pull request was found."
            ) from error

        try:
            pull_request = self.repo.create_pull(
                base=resolved_base_branch,
                head=branch_name,
                title=(
                    "Remove deprecated feature flag: "
                    f"{normalized_flag}"
                ),
                body=self._build_pr_body(
                    flag_name=normalized_flag,
                    changed_files=normalized_files,
                ),
            )

        except GithubException as error:
            if error.status != 422:
                raise

            # A concurrent process may have created the
            # PR after our branch was created.
            raced_pr = self._find_open_pr(
                branch_name=branch_name,
                base_branch=resolved_base_branch,
            )

            if raced_pr is not None:
                return (
                    self._result_from_existing_pr(
                        raced_pr,
                        branch_name,
                    )
                )

            raise DeliveryConflictError(
                "GitHub rejected pull request creation "
                "with status 422, and no reusable open "
                "pull request was found. The remediation "
                f"branch {branch_name!r} may require "
                "manual inspection."
            ) from error

        return PullRequestResult(
            url=pull_request.html_url,
            branch_name=branch_name,
            commit_sha=new_commit.sha,
            pr_number=getattr(
                pull_request,
                "number",
                None,
            ),
            reused_existing_pr=False,
        )

    def build_branch_name(
        self,
        flag_name: str,
    ) -> str:
        """
        Build a deterministic Git-friendly branch name.
        """
        normalized_flag = (
            self._validate_flag_name(
                flag_name
            )
        )

        slug = normalized_flag.lower()

        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            slug,
        )

        slug = slug.strip("-")

        slug = slug[
            : self.MAX_FLAG_SLUG_LENGTH
        ].rstrip("-")

        if not slug:
            raise ValueError(
                "flag_name does not contain "
                "characters usable in a branch."
            )

        return (
            f"{self.BRANCH_PREFIX}"
            f"{slug}"
        )

    def _normalize_changed_files(
        self,
        changed_files: Mapping[str, str],
    ) -> dict[str, str]:
        """
        Normalize and validate all changed files before
        making the first GitHub API call.
        """
        if not isinstance(
            changed_files,
            Mapping,
        ):
            raise TypeError(
                "changed_files must be a mapping."
            )

        if not changed_files:
            raise ValueError(
                "changed_files cannot be empty."
            )

        normalized_files: dict[
            str,
            str,
        ] = {}

        for raw_path, source_code in (
            changed_files.items()
        ):
            if not isinstance(raw_path, str):
                raise TypeError(
                    "Every changed file path must "
                    "be a string."
                )

            if not isinstance(
                source_code,
                str,
            ):
                raise TypeError(
                    f"Content for {raw_path!r} "
                    "must be a string."
                )

            normalized_path = (
                self._normalize_repo_path(
                    raw_path
                )
            )

            if (
                normalized_path
                in normalized_files
            ):
                raise ValueError(
                    "Duplicate normalized path "
                    "detected: "
                    f"{normalized_path}"
                )

            normalized_files[
                normalized_path
            ] = source_code

        # Deterministic blob/tree ordering.
        return dict(
            sorted(
                normalized_files.items()
            )
        )

    def _normalize_repo_path(
        self,
        raw_path: str,
    ) -> str:
        """
        Normalize a local-looking path into a safe
        repository-relative Git tree path.
        """
        if not raw_path.strip():
            raise ValueError(
                "File paths cannot be empty."
            )

        normalized = (
            raw_path
            .strip()
            .replace("\\", "/")
        )

        if "\x00" in normalized:
            raise ValueError(
                "File paths cannot contain "
                "null bytes."
            )

        if normalized.startswith("/"):
            raise ValueError(
                "File path must be "
                "repository-relative: "
                f"{raw_path}"
            )

        if re.match(
            r"^[a-zA-Z]:",
            normalized,
        ):
            raise ValueError(
                "Windows absolute paths are "
                "not allowed: "
                f"{raw_path}"
            )

        parts = normalized.split("/")

        if any(
            part in {"", ".", ".."}
            for part in parts
        ):
            raise ValueError(
                "Unsafe repository-relative path: "
                f"{raw_path}"
            )

        normalized_path = "/".join(parts)

        if (
            PurePosixPath(
                normalized_path
            ).suffix.lower()
            != ".py"
        ):
            raise ValueError(
                "Only Python files can be "
                "delivered: "
                f"{raw_path}"
            )

        return normalized_path

    def _validate_flag_name(
        self,
        flag_name: str,
    ) -> str:
        if not isinstance(flag_name, str):
            raise TypeError(
                "flag_name must be a string."
            )

        normalized = flag_name.strip()

        if not normalized:
            raise ValueError(
                "flag_name cannot be empty."
            )

        return normalized

    def _resolve_base_branch(
        self,
        base_branch: str | None,
    ) -> str:
        if base_branch is None:
            resolved = str(
                self.repo.default_branch
            ).strip()

        elif isinstance(base_branch, str):
            resolved = base_branch.strip()

        else:
            raise TypeError(
                "base_branch must be a "
                "string or None."
            )

        if not resolved:
            raise ValueError(
                "The repository does not have "
                "a usable base branch."
            )

        return resolved

    def _create_tree_elements(
        self,
        changed_files: Mapping[str, str],
    ) -> list[InputGitTreeElement]:
        elements: list[
            InputGitTreeElement
        ] = []

        for path, source_code in (
            changed_files.items()
        ):
            blob = self.repo.create_git_blob(
                content=source_code,
                encoding="utf-8",
            )

            elements.append(
                InputGitTreeElement(
                    path=path,
                    mode="100644",
                    type="blob",
                    sha=blob.sha,
                )
            )

        return elements

    def _branch_exists(
        self,
        branch_name: str,
    ) -> bool:
        try:
            self.repo.get_git_ref(
                f"heads/{branch_name}"
            )
            return True

        except GithubException as error:
            if error.status == 404:
                return False

            raise

    def _find_open_pr(
        self,
        branch_name: str,
        base_branch: str,
    ) -> Any | None:
        owner_login = (
            self._repository_owner_login()
        )

        pull_requests = self.repo.get_pulls(
            state="open",
            base=base_branch,
            head=(
                f"{owner_login}:"
                f"{branch_name}"
            ),
        )

        return next(
            iter(pull_requests),
            None,
        )

    def _repository_owner_login(
        self,
    ) -> str:
        owner = getattr(
            self.repo,
            "owner",
            None,
        )

        owner_login = getattr(
            owner,
            "login",
            None,
        )

        if owner_login:
            return str(owner_login)

        full_name = str(
            getattr(
                self.repo,
                "full_name",
                "",
            )
        )

        if "/" in full_name:
            return full_name.split(
                "/",
                maxsplit=1,
            )[0]

        raise DeliveryError(
            "Could not determine the "
            "repository owner's login."
        )

    def _result_from_existing_pr(
        self,
        pull_request: Any,
        branch_name: str,
    ) -> PullRequestResult:
        head = getattr(
            pull_request,
            "head",
            None,
        )

        commit_sha = getattr(
            head,
            "sha",
            None,
        )

        if not commit_sha:
            branch_ref = (
                self.repo.get_git_ref(
                    f"heads/{branch_name}"
                )
            )

            commit_sha = (
                branch_ref.object.sha
            )

        return PullRequestResult(
            url=pull_request.html_url,
            branch_name=branch_name,
            commit_sha=str(commit_sha),
            pr_number=getattr(
                pull_request,
                "number",
                None,
            ),
            reused_existing_pr=True,
        )

    def _build_pr_body(
        self,
        flag_name: str,
        changed_files: Mapping[str, str],
    ) -> str:
        file_list = "\n".join(
            f"- `{path}`"
            for path in sorted(
                changed_files
            )
        )

        return (
            "Automated remediation generated by "
            "Feature Flag Undertaker.\n\n"
            f"Deprecated feature flag: "
            f"`{flag_name}`\n\n"
            "Changed files:\n"
            f"{file_list}\n\n"
            "The source transformation was "
            "produced by the deterministic LibCST "
            "remediation layer. This pull request "
            "must still be reviewed by a maintainer "
            "before merging."
        )