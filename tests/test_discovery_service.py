from pathlib import Path

from project.discovery.schema import (
    DiscoveryPlan,
)
from project.discovery.service import (
    DiscoveryService,
)


class FakeDiscoveryClient:
    def __init__(self):
        self.calls = []

    def discover(self, **kwargs):
        self.calls.append(kwargs)

        return DiscoveryPlan(
            targets=[]
        )


def test_service_scans_extracts_and_calls_discovery(
    tmp_path: Path,
):
    (tmp_path / "app.py").write_text(
        (
            "if OLD_FLAG:\n"
            "    run_new()\n"
            "else:\n"
            "    run_old()\n"
        ),
        encoding="utf-8",
    )

    tests_directory = (
        tmp_path / "tests"
    )
    tests_directory.mkdir()

    (
        tests_directory
        / "test_app.py"
    ).write_text(
        (
            "if TEST_FLAG:\n"
            "    pass\n"
        ),
        encoding="utf-8",
    )

    client = FakeDiscoveryClient()

    result = DiscoveryService(
        tmp_path,
        client,
    ).discover(
        (
            "OLD_FLAG rollout was "
            "permanently disabled."
        )
    )

    assert (
        result.repository_allowlist
        == frozenset(
            {
                "app.py",
            }
        )
    )

    assert [
        candidate.flag_name
        for candidate in result.candidates
    ] == [
        "OLD_FLAG",
    ]

    assert result.plan.targets == []

    assert (
        client.calls[0]
        ["business_context"]
        .startswith("OLD_FLAG")
    )