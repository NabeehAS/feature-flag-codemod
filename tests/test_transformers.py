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


def test_if_removal_no_else_true():
    source = "if FF_ACTIVE:\n    x = 1\nprint('done')" 
    expected = "x = 1\nprint('done')"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_if_removal_no_else_false():
    source = "if FF_ACTIVE:\n    x = 1\nprint('done')"
    expected = "print('done')"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()


def test_if_inside_function_true():
    source = "def handler():\n    if FF_ACTIVE:\n        x = 1\n    print('done')"
    expected = "def handler():\n    x = 1\n    print('done')"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_if_inside_function_false():
    source = "def handler():\n    if FF_ACTIVE:\n        x = 1\n    print('done')"
    expected = "def handler():\n    print('done')"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()


def test_if_removal_no_else_true():
    source = "if FF_ACTIVE:\n    x = 1\nprint('done')"
    expected = "x = 1\nprint('done')"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_if_removal_no_else_false():
    source = "if FF_ACTIVE:\n    x = 1\nprint('done')"
    expected = "print('done')"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()


def test_nested_if_true():
    source = "if FF_ACTIVE:\n    if DEBUG:\n        x = 1"
    expected = "if DEBUG:\n    x = 1"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_nested_if_false():
    source = "if FF_ACTIVE:\n    if DEBUG:\n        x = 1"
    expected = ""
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()


def test_if_with_elif_target_is_if_false():
    # If the main flag is False, the 'elif' should be promoted to an 'if'
    source = "if FF_ACTIVE:\n    x = 1\nelif OTHER_FLAG:\n    x = 2\nelse:\n    x = 3"
    expected = "if OTHER_FLAG:\n    x = 2\nelse:\n    x = 3"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()

def test_elif_chain_target_is_elif_true():
    # If the target flag is in the elif and is True, the elif block becomes the else block
    source = "if OTHER_FLAG:\n    x = 1\nelif FF_ACTIVE:\n    x = 2\nelse:\n    x = 3"
    expected = "if OTHER_FLAG:\n    x = 1\nelse:\n    x = 2"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_elif_chain_target_is_elif_false():
    # If the target flag is in the elif and is False, the elif is removed entirely
    source = "if OTHER_FLAG:\n    x = 1\nelif FF_ACTIVE:\n    x = 2\nelse:\n    x = 3"
    expected = "if OTHER_FLAG:\n    x = 1\nelse:\n    x = 3"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()


def test_empty_function_after_removal_false():
    # If an 'if' statement is the ONLY thing inside a function, removing it
    # leaves an empty function, which is a SyntaxError in Python. 
    # LibCST needs to know to insert a 'pass' statement.
    source = "def do_nothing():\n    if FF_ACTIVE:\n        x = 1"
    expected = "def do_nothing():\n    pass"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()

def test_ternary_expression_true():
    # Inline if/else statements (ternary operators) are NOT cst.If nodes.
    # They are cst.IfExp (If Expressions). The current mutator is completely blind to them.
    source = "status = 'active' if FF_ACTIVE else 'inactive'"
    expected = "status = 'active'"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()

def test_ternary_expression_false():
    source = "status = 'active' if FF_ACTIVE else 'inactive'"
    expected = "status = 'inactive'"
    result = apply_mutation(source, "FF_ACTIVE", False)
    assert result.strip() == expected.strip()

def test_composite_condition_ignored():
    # If the flag is part of a complex boolean (e.g., 'if FF_ACTIVE and is_admin:'),
    # blindly flattening it could break the secondary logic. For V0, our engine should 
    # intentionally IGNORE these and leave them untouched for manual review.
    source = "if FF_ACTIVE and is_admin:\n    x = 1"
    expected = "if FF_ACTIVE and is_admin:\n    x = 1"
    result = apply_mutation(source, "FF_ACTIVE", True)
    assert result.strip() == expected.strip()
