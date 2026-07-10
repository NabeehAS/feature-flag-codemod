from types import SimpleNamespace

import pytest
from github import GithubException

from project.delivery.github_ops import (
    DeliveryConflictError,
    GitOpsDelivery,
)


def github_error(status: int, message: str) -> GithubException:
    """Create a realistic mocked PyGithub API exception."""
    return GithubException(
        status=status,
        data={"message": message},
        headers={},
    )


class FakeGitRef:
    def __init__(self, sha: str) -> None:
        self.object = SimpleNamespace(sha=sha)


class FakeBaseCommit:
    def __init__(
        self,
        sha: str = "base-commit-sha",
        tree_sha: str = "base-tree-sha",
    ) -> None:
        self.sha = sha
        self.tree = SimpleNamespace(sha=tree_sha)


class FakeBlob:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class FakeTree:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class FakeCreatedCommit:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class FakePullRequest:
    def __init__(
        self,
        html_url: str = "https://github.com/example/repo/pull/1",
        number: int = 1,
        head_sha: str = "pull-head-sha",
    ) -> None:
        self.html_url = html_url
        self.number = number
        self.head = SimpleNamespace(sha=head_sha)


class FakeRepo:
    def __init__(
        self,
        *,
        default_branch: str = "main",
        base_branches: set[str] | None = None,
        branch_exists: bool = False,
        pull_responses=None,
        branch_creation_status: int | None = None,
        pull_creation_status: int | None = None,
    ) -> None:
        self.default_branch = default_branch

        self.base_branches = set(
            base_branches or {default_branch}
        )
        self.base_branches.add(default_branch)

        self.full_name = "example/repo"
        self.owner = SimpleNamespace(login="example")

        self.branch_exists = branch_exists
        self.pull_responses = list(pull_responses or [])
        self.branch_creation_status = branch_creation_status
        self.pull_creation_status = pull_creation_status

        self.base_ref = FakeGitRef("base-ref-sha")
        self.existing_branch_ref = FakeGitRef(
            "existing-branch-sha"
        )
        self.base_commit = FakeBaseCommit()
        self.base_tree = FakeTree("base-tree-object-sha")

        self.get_pulls_calls = []
        self.get_git_ref_calls = []

        self.created_blobs = []
        self.created_tree_args = None
        self.created_commit_args = None
        self.created_ref_args = None
        self.created_pull_args = None

    def get_pulls(self, **kwargs):
        self.get_pulls_calls.append(kwargs)

        if self.pull_responses:
            return self.pull_responses.pop(0)

        return []

    def get_git_ref(self, ref: str):
        self.get_git_ref_calls.append(ref)

        if ref.startswith("heads/remediation/"):
            if self.branch_exists:
                return self.existing_branch_ref

            raise github_error(
                404,
                "Reference not found",
            )

        if ref.startswith("heads/"):
            branch_name = ref.removeprefix("heads/")

            if branch_name in self.base_branches:
                return self.base_ref

            raise github_error(
                404,
                "Base branch not found",
            )

        raise AssertionError(
            f"Unexpected Git reference requested: {ref}"
        )

    def get_git_commit(self, sha: str):
        assert sha == "base-ref-sha"
        return self.base_commit

    def get_git_tree(self, sha: str):
        assert sha == "base-tree-sha"
        return self.base_tree

    def create_git_blob(
        self,
        content: str,
        encoding: str,
    ):
        self.created_blobs.append(
            {
                "content": content,
                "encoding": encoding,
            }
        )

        return FakeBlob(
            f"blob-{len(self.created_blobs)}"
        )

    def create_git_tree(self, tree, base_tree):
        # InputGitTreeElement does not expose public
        # path/mode/type/sha properties. PyGithub serializes
        # the object through its _identity dictionary.
        self.created_tree_args = {
            "tree": [
                element._identity
                for element in tree
            ],
            "base_tree": base_tree,
        }

        return FakeTree("new-tree-sha")

    def create_git_commit(
        self,
        message: str,
        tree,
        parents,
    ):
        self.created_commit_args = {
            "message": message,
            "tree": tree,
            "parents": parents,
        }

        return FakeCreatedCommit(
            "new-commit-sha"
        )

    def create_git_ref(
        self,
        ref: str,
        sha: str,
    ):
        self.created_ref_args = {
            "ref": ref,
            "sha": sha,
        }

        if self.branch_creation_status is not None:
            raise github_error(
                self.branch_creation_status,
                "Could not create branch",
            )

        return FakeGitRef(sha)

    def create_pull(self, **kwargs):
        self.created_pull_args = kwargs

        if self.pull_creation_status is not None:
            raise github_error(
                self.pull_creation_status,
                "Could not create pull request",
            )

        return FakePullRequest(
            head_sha="new-commit-sha",
        )


def test_creates_multiple_files_in_one_commit():
    repo = FakeRepo()
    delivery = GitOpsDelivery(repo=repo)

    result = delivery.create_remediation_pr(
        changed_files={
            "project/app.py": (
                "print('updated app')\n"
            ),
            "project/routes.py": (
                "print('updated routes')\n"
            ),
        },
        flag_name="OLD_FLAG",
    )

    assert (
        result.url
        == "https://github.com/example/repo/pull/1"
    )
    assert (
        result.branch_name
        == "remediation/remove-old-flag"
    )
    assert result.commit_sha == "new-commit-sha"
    assert result.pr_number == 1
    assert result.reused_existing_pr is False

    assert repo.created_blobs == [
        {
            "content": "print('updated app')\n",
            "encoding": "utf-8",
        },
        {
            "content": "print('updated routes')\n",
            "encoding": "utf-8",
        },
    ]

    assert repo.created_tree_args["tree"] == [
        {
            "path": "project/app.py",
            "mode": "100644",
            "type": "blob",
            "sha": "blob-1",
        },
        {
            "path": "project/routes.py",
            "mode": "100644",
            "type": "blob",
            "sha": "blob-2",
        },
    ]

    assert (
        repo.created_tree_args["base_tree"]
        is repo.base_tree
    )

    assert repo.created_commit_args["message"] == (
        "chore: remove deprecated feature flag "
        "OLD_FLAG"
    )

    assert (
        repo.created_commit_args["tree"].sha
        == "new-tree-sha"
    )

    assert (
        repo.created_commit_args["parents"]
        == [repo.base_commit]
    )

    assert repo.created_ref_args == {
        "ref": (
            "refs/heads/"
            "remediation/remove-old-flag"
        ),
        "sha": "new-commit-sha",
    }

    assert (
        repo.created_pull_args["base"]
        == "main"
    )

    assert repo.created_pull_args["head"] == (
        "remediation/remove-old-flag"
    )


def test_uses_repository_default_branch():
    repo = FakeRepo(
        default_branch="develop",
    )
    delivery = GitOpsDelivery(repo=repo)

    delivery.create_remediation_pr(
        changed_files={
            "app.py": "print('updated')\n",
        },
        flag_name="OLD_FLAG",
    )

    assert (
        "heads/develop"
        in repo.get_git_ref_calls
    )

    assert (
        repo.created_pull_args["base"]
        == "develop"
    )


def test_explicit_base_branch_overrides_default():
    repo = FakeRepo(
        default_branch="develop",
        base_branches={
            "develop",
            "release",
        },
    )
    delivery = GitOpsDelivery(repo=repo)

    delivery.create_remediation_pr(
        changed_files={
            "app.py": "print('updated')\n",
        },
        flag_name="OLD_FLAG",
        base_branch="release",
    )

    assert (
        "heads/release"
        in repo.get_git_ref_calls
    )

    assert (
        repo.created_pull_args["base"]
        == "release"
    )


def test_normalizes_windows_paths_before_creating_tree():
    repo = FakeRepo()
    delivery = GitOpsDelivery(repo=repo)

    delivery.create_remediation_pr(
        changed_files={
            r"project\core\mutator.py": (
                "print('updated')\n"
            ),
        },
        flag_name="OLD_FLAG",
    )

    assert (
        repo.created_tree_args["tree"][0]["path"]
        == "project/core/mutator.py"
    )


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "/absolute/app.py",
        r"C:\repository\app.py",
        "../outside.py",
        "project/../outside.py",
        "project/./app.py",
        "project//app.py",
        "README.md",
    ],
)
def test_rejects_invalid_paths_before_any_remote_write(
    bad_path,
):
    repo = FakeRepo()
    delivery = GitOpsDelivery(repo=repo)

    with pytest.raises(
        (TypeError, ValueError)
    ):
        delivery.create_remediation_pr(
            changed_files={
                bad_path: "content",
            },
            flag_name="OLD_FLAG",
        )

    assert repo.get_pulls_calls == []
    assert repo.created_blobs == []
    assert repo.created_tree_args is None
    assert repo.created_commit_args is None
    assert repo.created_ref_args is None
    assert repo.created_pull_args is None


def test_rejects_duplicate_paths_after_normalization():
    repo = FakeRepo()
    delivery = GitOpsDelivery(repo=repo)

    with pytest.raises(
        ValueError,
        match="Duplicate normalized path",
    ):
        delivery.create_remediation_pr(
            changed_files={
                r"project\app.py": "first",
                "project/app.py": "second",
            },
            flag_name="OLD_FLAG",
        )

    assert repo.created_blobs == []


def test_returns_existing_open_pr_without_writes():
    existing_pr = FakePullRequest(
        html_url=(
            "https://github.com/"
            "example/repo/pull/7"
        ),
        number=7,
        head_sha="existing-pr-commit",
    )

    repo = FakeRepo(
        pull_responses=[
            [existing_pr],
        ],
    )
    delivery = GitOpsDelivery(repo=repo)

    result = delivery.create_remediation_pr(
        changed_files={
            "app.py": "print('updated')\n",
        },
        flag_name="OLD_FLAG",
    )

    assert result.url == (
        "https://github.com/"
        "example/repo/pull/7"
    )
    assert (
        result.commit_sha
        == "existing-pr-commit"
    )
    assert result.pr_number == 7
    assert result.reused_existing_pr is True

    assert repo.created_blobs == []
    assert repo.created_tree_args is None
    assert repo.created_commit_args is None
    assert repo.created_ref_args is None
    assert repo.created_pull_args is None


def test_existing_pr_query_uses_owner_branch_filter():
    repo = FakeRepo(
        pull_responses=[[]],
    )
    delivery = GitOpsDelivery(repo=repo)

    delivery.create_remediation_pr(
        changed_files={
            "app.py": "print('updated')\n",
        },
        flag_name="OLD_FLAG",
    )

    assert repo.get_pulls_calls[0] == {
        "state": "open",
        "base": "main",
        "head": (
            "example:"
            "remediation/remove-old-flag"
        ),
    }


def test_rejects_existing_branch_without_open_pr():
    repo = FakeRepo(
        branch_exists=True,
        pull_responses=[[]],
    )
    delivery = GitOpsDelivery(repo=repo)

    with pytest.raises(
        DeliveryConflictError,
        match=(
            "already exists but has no "
            "open pull request"
        ),
    ):
        delivery.create_remediation_pr(
            changed_files={
                "app.py": "print('updated')\n",
            },
            flag_name="OLD_FLAG",
        )

    assert repo.created_blobs == []
    assert repo.created_ref_args is None


def test_branch_creation_422_rechecks_for_existing_pr():
    existing_pr = FakePullRequest(
        html_url=(
            "https://github.com/"
            "example/repo/pull/9"
        ),
        number=9,
        head_sha="racing-commit-sha",
    )

    repo = FakeRepo(
        pull_responses=[
            [],
            [existing_pr],
        ],
        branch_creation_status=422,
    )
    delivery = GitOpsDelivery(repo=repo)

    result = delivery.create_remediation_pr(
        changed_files={
            "app.py": "print('updated')\n",
        },
        flag_name="OLD_FLAG",
    )

    assert result.url == (
        "https://github.com/"
        "example/repo/pull/9"
    )
    assert result.reused_existing_pr is True


def test_pull_creation_422_rechecks_for_existing_pr():
    existing_pr = FakePullRequest(
        html_url=(
            "https://github.com/"
            "example/repo/pull/10"
        ),
        number=10,
        head_sha="new-commit-sha",
    )

    repo = FakeRepo(
        pull_responses=[
            [],
            [existing_pr],
        ],
        pull_creation_status=422,
    )
    delivery = GitOpsDelivery(repo=repo)

    result = delivery.create_remediation_pr(
        changed_files={
            "app.py": "print('updated')\n",
        },
        flag_name="OLD_FLAG",
    )

    assert result.url == (
        "https://github.com/"
        "example/repo/pull/10"
    )
    assert result.reused_existing_pr is True


def test_pull_creation_422_without_pr_raises_conflict():
    repo = FakeRepo(
        pull_responses=[
            [],
            [],
        ],
        pull_creation_status=422,
    )
    delivery = GitOpsDelivery(repo=repo)

    with pytest.raises(
        DeliveryConflictError,
        match=(
            "GitHub rejected pull request "
            "creation"
        ),
    ):
        delivery.create_remediation_pr(
            changed_files={
                "app.py": "print('updated')\n",
            },
            flag_name="OLD_FLAG",
        )


def test_rejects_empty_changed_files():
    delivery = GitOpsDelivery(
        repo=FakeRepo()
    )

    with pytest.raises(
        ValueError,
        match="changed_files cannot be empty",
    ):
        delivery.create_remediation_pr(
            changed_files={},
            flag_name="OLD_FLAG",
        )


def test_rejects_empty_flag_name():
    delivery = GitOpsDelivery(
        repo=FakeRepo()
    )

    with pytest.raises(
        ValueError,
        match="flag_name cannot be empty",
    ):
        delivery.create_remediation_pr(
            changed_files={
                "app.py": "content",
            },
            flag_name="   ",
        )


def test_rejects_non_string_base_branch():
    delivery = GitOpsDelivery(
        repo=FakeRepo()
    )

    with pytest.raises(
        TypeError,
        match="base_branch",
    ):
        delivery.create_remediation_pr(
            changed_files={
                "app.py": "content",
            },
            flag_name="OLD_FLAG",
            base_branch=123,
        )


def test_branch_name_is_deterministic_and_sanitized():
    delivery = GitOpsDelivery(
        repo=FakeRepo()
    )

    assert delivery.build_branch_name(
        "Enable New Dashboard!"
    ) == (
        "remediation/"
        "remove-enable-new-dashboard"
    )


def test_branch_name_has_bounded_length():
    delivery = GitOpsDelivery(
        repo=FakeRepo()
    )

    branch_name = delivery.build_branch_name(
        "A" * 300
    )

    assert branch_name.startswith(
        "remediation/remove-"
    )

    assert len(branch_name) <= (
        len("remediation/remove-") + 80
    )