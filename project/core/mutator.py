import libcst as cst
from libcst import matchers as m
from typing import Union

class FeatureFlagRemover(cst.CSTTransformer):
    """Deterministically removes feature flag conditionals."""
    def __init__(self, target_flag: str, flag_state: bool) -> None:
        super().__init__()
        self.target_flag = target_flag
        self.flag_state = flag_state

    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> Union[cst.CSTNode, cst.RemovalSentinel, cst.FlattenSentinel]:
        if m.matches(original_node.test, m.Name(value=self.target_flag)):
            if self.flag_state:
                # Flag is True: Replace the entire 'if' with its body
                return cst.FlattenSentinel(updated_node.body.body)
            else:
                # Flag is False: Return orelse block if it exists, else remove
                if updated_node.orelse:
                    return cst.FlattenSentinel(updated_node.orelse.body.body)
                return cst.RemovalSentinel.REMOVE
        return updated_node

def apply_mutation(source_code: str, flag_name: str, flag_state: bool) -> str:
    module = cst.parse_module(source_code)
    transformer = FeatureFlagRemover(flag_name, flag_state)
    modified_module = module.visit(transformer)
    return modified_module.code