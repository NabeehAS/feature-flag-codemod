from project.core.mutator import apply_mutation

def test_if_removal_true():
    source = "if FF_ACTIVE:\n    x = 1\nelse:\n    x = 2"
    expected = "x = 1"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_if_removal_false():
    source = "if FF_ACTIVE:\n    x = 1\nelse:\n    x = 2"
    expected = "x = 2"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()