from project.core.cleanup import apply_cleanup
from project.core.mutator import apply_mutation


def mutate_and_cleanup(source: str, flag_name: str, flag_state: bool) -> str:
    mutated = apply_mutation(source, flag_name, flag_state)
    return apply_cleanup(mutated)


def test_removes_import_used_only_by_deleted_true_branch():
    source = (
        "from service import old_func\n"
        "if OLD_FLAG:\n"
        "    old_func()\n"
        "else:\n"
        "    keep()\n"
    )

    expected = "keep()\n"

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_keeps_import_used_elsewhere():
    source = (
        "from service import old_func\n"
        "if OLD_FLAG:\n"
        "    old_func()\n"
        "old_func()\n"
    )

    expected = (
        "from service import old_func\n"
        "old_func()\n"
    )

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_reduces_from_import_without_deleting_whole_line():
    source = (
        "from service import old_func, keep_func\n"
        "if OLD_FLAG:\n"
        "    old_func()\n"
        "keep_func()\n"
    )

    expected = (
        "from service import keep_func\n"
        "keep_func()\n"
    )

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_removes_regular_import_when_unused_after_mutation():
    source = (
        "import old_service\n"
        "if OLD_FLAG:\n"
        "    old_service.run()\n"
        "else:\n"
        "    keep()\n"
    )

    expected = "keep()\n"

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_keeps_regular_import_when_still_used():
    source = (
        "import old_service\n"
        "if OLD_FLAG:\n"
        "    old_service.run()\n"
        "old_service.audit()\n"
    )

    expected = (
        "import old_service\n"
        "old_service.audit()\n"
    )

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_removes_aliased_import_when_alias_is_unused():
    source = (
        "from service import old_func as old\n"
        "if OLD_FLAG:\n"
        "    old()\n"
        "else:\n"
        "    keep()\n"
    )

    expected = "keep()\n"

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_keeps_aliased_import_when_alias_is_still_used():
    source = (
        "from service import old_func as old\n"
        "if OLD_FLAG:\n"
        "    old()\n"
        "old()\n"
    )

    expected = (
        "from service import old_func as old\n"
        "old()\n"
    )

    assert mutate_and_cleanup(source, "OLD_FLAG", False) == expected


def test_keeps_star_imports_because_they_are_unsafe_to_analyze():
    source = (
        "from service import *\n"
        "run()\n"
    )

    assert apply_cleanup(source) == source