import libcst as cst
from libcst import matchers as m
from typing import Union

class FeatureFlagRemover(cst.CSTTransformer):
    """Deterministically removes feature flag conditionals."""
    def __init__(self, target_flag: str, flag_state: bool) -> None:
        super().__init__()
        self.target_flag = target_flag
        self.flag_state = flag_state
        # Track memory IDs of If nodes that act as elifs
        self.elif_nodes = set()

    def visit_If(self, node: cst.If) -> bool:
        # If this If node has an orelse that is also an If node, 
        # that child is an 'elif' in the CST.
        if isinstance(node.orelse, cst.If):
            self.elif_nodes.add(id(node.orelse))
        return True

    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> Union[cst.CSTNode, cst.RemovalSentinel, cst.FlattenSentinel]:
        is_elif = id(original_node) in self.elif_nodes

        if m.matches(original_node.test, m.Name(value=self.target_flag)):
            if self.flag_state:
                # Flag is True
                if is_elif:
                    # It's an elif. It becomes an 'else' block, dropping any subsequent branches.
                    return cst.Else(body=updated_node.body)
                else:
                    # It's a top-level if. Replace the entire statement with its inner body.
                    return cst.FlattenSentinel(updated_node.body.body)
            else:
                # Flag is False
                if is_elif:
                    # It's an elif. Skip it, pull up its orelse branch (If, Else, or None).
                    return updated_node.orelse if updated_node.orelse is not None else cst.RemovalSentinel.REMOVE
                else:
                    # It's a top-level if.
                    if updated_node.orelse:
                        if isinstance(updated_node.orelse, cst.If):
                            # The orelse is an elif. It becomes the new top-level If.
                            return updated_node.orelse
                        elif isinstance(updated_node.orelse, cst.Else):
                            # The orelse is an else. Unpack its body to the top level.
                            return cst.FlattenSentinel(updated_node.orelse.body.body)
                    
                    # No orelse branch exists, completely remove the top-level if.
                    return cst.RemovalSentinel.REMOVE
                    
        return updated_node

    def leave_IfExp(self, original_node: cst.IfExp, updated_node: cst.IfExp) -> Union[cst.CSTNode, cst.RemovalSentinel]:
        if m.matches(original_node.test, m.Name(value=self.target_flag)):
            if self.flag_state:
                # Flag is True: Replace "A if Flag else B" with "A"
                return updated_node.body
            else:
                # Flag is False: Replace "A if Flag else B" with "B"
                return updated_node.orelse
        return updated_node

def apply_mutation(source_code: str, flag_name: str, flag_state: bool) -> str:
    module = cst.parse_module(source_code)
    transformer = FeatureFlagRemover(flag_name, flag_state)
    modified_module = module.visit(transformer)
    return modified_module.code