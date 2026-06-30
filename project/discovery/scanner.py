import os
from pathlib import Path
from typing import Iterator, List

class RepoScanner:
    def __init__(self, root_dir: Path, ignore_patterns: List[str] = None) -> None:
        self.root_dir = root_dir.resolve()
        # Use a set for O(1) lookups. Added common heavy directories.
        self.ignore_patterns = set(ignore_patterns or [
            "tests", "venv", ".git", "__pycache__", ".tox", "node_modules", "build", "dist"
        ])

    def get_python_files(self) -> Iterator[Path]:
        """
        Yields Python files using an optimized top-down directory walk.
        We use an Iterator (generator) so we stream results to memory one at a time,
        preventing Out-Of-Memory (OOM) errors on massive monorepos.
        """
        # os.walk with topdown=True allows us to modify the 'dirs' list in-place.
        for root, dirs, files in os.walk(self.root_dir, topdown=True):
            
            # 1. PRUNING THE TREE: Drop hidden folders and ignored folders
            # By slicing dirs[:], we modify the list os.walk is currently iterating over
            dirs[:] = [
                d for d in dirs 
                if d not in self.ignore_patterns and not d.startswith('.')
            ]

            # 2. YIELDING VALID FILES
            for file in files:
                if file.endswith('.py'):
                    yield Path(root) / file