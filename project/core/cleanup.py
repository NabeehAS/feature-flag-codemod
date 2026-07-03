from __future__ import annotations

from typing import List, Optional, Sequence, Set, Union

import libcst as cst


class UsedNameCollector(cst.CSTVisitor):
    """
    Collects names that are used outside import statements.

    This is intentionally conservative:
    - If a name appears anywhere outside an import, we consider it used.
    - That may keep some imports that are technically unused.
    - But it avoids aggressive removals that could break code.
    """

    def __init__(self) -> None:
        self.used_names: Set[str] = set()

    def visit_Import(self, node: cst.Import) -> bool:
        # Do not count names inside `import x`.
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        # Do not count names inside `from x import y`.
        return False

    def visit_Name(self, node: cst.Name) -> None:
        self.used_names.add(node.value)


class UnusedImportRemover(cst.CSTTransformer):
    """
    Removes import aliases whose local binding no longer appears in the file.

    Examples:
        from service import old_func, keep_func

    If old_func is no longer used but keep_func is still used, this becomes:

        from service import keep_func
    """

    def __init__(self, used_names: Set[str]) -> None:
        super().__init__()
        self.used_names = used_names

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> Union[cst.SimpleStatementLine, cst.RemovalSentinel]:
        new_body: List[cst.BaseSmallStatement] = []

        for statement in updated_node.body:
            if isinstance(statement, cst.Import):
                cleaned_import = self._clean_import(statement)
                if cleaned_import is not None:
                    new_body.append(cleaned_import)

            elif isinstance(statement, cst.ImportFrom):
                cleaned_import_from = self._clean_import_from(statement)
                if cleaned_import_from is not None:
                    new_body.append(cleaned_import_from)

            else:
                new_body.append(statement)

        if not new_body:
            return cst.RemovalSentinel.REMOVE

        return updated_node.with_changes(body=tuple(new_body))

    def _clean_import(self, node: cst.Import) -> Optional[cst.Import]:
        kept_aliases = self._filter_aliases(
            aliases=node.names,
            import_from=False,
        )

        if not kept_aliases:
            return None

        return node.with_changes(names=kept_aliases)

    def _clean_import_from(self, node: cst.ImportFrom) -> Optional[cst.ImportFrom]:
        # Do not touch `from module import *`.
        if isinstance(node.names, cst.ImportStar):
            return node

        kept_aliases = self._filter_aliases(
            aliases=node.names,
            import_from=True,
        )

        if not kept_aliases:
            return None

        return node.with_changes(names=kept_aliases)

    def _filter_aliases(
        self,
        aliases: Sequence[cst.ImportAlias],
        import_from: bool,
    ) -> Sequence[cst.ImportAlias]:
        kept_aliases: List[cst.ImportAlias] = []

        for alias in aliases:
            local_name = self._get_local_binding_name(alias, import_from=import_from)

            # If we cannot safely determine the local binding, keep it.
            if local_name is None:
                kept_aliases.append(alias)
                continue

            if local_name in self.used_names:
                kept_aliases.append(alias)

        if kept_aliases:
            # If we removed aliases from the end of an import list, prevent:
            # from service import keep_func,
            kept_aliases[-1] = kept_aliases[-1].with_changes(
                comma=cst.MaybeSentinel.DEFAULT
            )

        return tuple(kept_aliases)

    def _get_local_binding_name(
        self,
        alias: cst.ImportAlias,
        import_from: bool,
    ) -> Optional[str]:
        if alias.asname is not None:
            return alias.asname.name.value

        parts = self._get_dotted_name_parts(alias.name)

        if not parts:
            return None

        if import_from:
            # from service import old_func
            # local binding is old_func
            return parts[-1]

        # import package.submodule
        # local binding is package
        return parts[0]

    def _get_dotted_name_parts(self, node: cst.CSTNode) -> List[str]:
        if isinstance(node, cst.Name):
            return [node.value]

        if isinstance(node, cst.Attribute):
            return self._get_dotted_name_parts(node.value) + [node.attr.value]

        return []


def apply_cleanup(source_code: str) -> str:
    """
    Remove imports that are no longer referenced after mutation.

    This should be called after apply_mutation(), not before.
    """
    module = cst.parse_module(source_code)

    collector = UsedNameCollector()
    module.visit(collector)

    cleaner = UnusedImportRemover(collector.used_names)
    cleaned_module = module.visit(cleaner)

    return cleaned_module.code