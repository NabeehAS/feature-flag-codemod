import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from project.core.cleanup import apply_cleanup
from project.core.mutator import apply_mutation
from project.discovery.scanner import RepoScanner
from project.validation.runner import LocalTestRunner


class ProcessResult(str, Enum):
    """Represents the outcome of processing a single file."""

    MUTATED = "mutated"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True)
class ProcessOutcome:
    """Detailed result for one processed file."""

    result: ProcessResult
    original_source: Optional[str] = None
    error: Optional[str] = None


def process_file(file_path: Path, flag: str, state_bool: bool) -> ProcessOutcome:
    """
    Process a single Python file.

    Flow:
    1. Read original source.
    2. Apply deterministic feature-flag mutation.
    3. If nothing changed, do not run cleanup.
    4. If changed, run import cleanup.
    5. Write cleaned result to disk.
    6. Return the original source so validation can roll back if needed.
    """
    try:
        original_source = file_path.read_text(encoding="utf-8")
        mutated_source = apply_mutation(original_source, flag, state_bool)

        if original_source == mutated_source:
            return ProcessOutcome(result=ProcessResult.UNCHANGED)

        cleaned_source = apply_cleanup(mutated_source)
        file_path.write_text(cleaned_source, encoding="utf-8")

        print(f"[*] Mutated: {file_path}")

        return ProcessOutcome(
            result=ProcessResult.MUTATED,
            original_source=original_source,
        )

    except Exception as e:
        error = str(e)
        print(f"[!] Failed {file_path}: {error}", file=sys.stderr)

        return ProcessOutcome(
            result=ProcessResult.FAILED,
            error=error,
        )


def rollback_files(original_sources: Dict[Path, str]) -> None:
    """Restore every mutated file to its original source."""
    for file_path, original_source in original_sources.items():
        file_path.write_text(original_source, encoding="utf-8")
        print(f"[rollback] Restored: {file_path}")


def run_validation(repo_root: Path, timeout_seconds: int) -> bool:
    """Run the repository's pytest suite."""
    print(f"Running validation from: {repo_root}")

    runner = LocalTestRunner(
        repo_root=repo_root,
        timeout_seconds=timeout_seconds,
    )

    result = runner.run()

    if result.output:
        print(result.output)

    if result.passed:
        print("Validation passed.")
        return True

    print("Validation failed.", file=sys.stderr)
    return False


def resolve_validation_root(
    file_path: Optional[Path],
    dir_path: Optional[Path],
    repo_root: Optional[Path],
) -> Path:
    """
    Decide where pytest should run.

    Priority:
    1. Explicit --repo-root
    2. --dir target
    3. Parent directory of --file target
    """
    if repo_root is not None:
        return repo_root.resolve()

    if dir_path is not None:
        return dir_path.resolve()

    if file_path is not None:
        return file_path.parent.resolve()

    raise ValueError("Could not resolve validation root.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic Feature Flag Remover"
    )

    parser.add_argument(
        "--flag",
        type=str,
        required=True,
        help="Exact flag variable name",
    )

    parser.add_argument(
        "--state",
        type=str,
        choices=["true", "false"],
        required=True,
        help="Target state of the flag",
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run pytest after mutation and roll back changes if validation fails.",
    )

    parser.add_argument(
        "--repo-root",
        type=Path,
        help=(
            "Directory where validation should run. "
            "Defaults to --dir or the parent directory of --file."
        ),
    )

    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Maximum number of seconds to allow pytest validation to run.",
    )

    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument(
        "--file",
        type=Path,
        help="Target a single Python file",
    )

    group.add_argument(
        "--dir",
        type=Path,
        help="Target an entire repository directory",
    )

    args = parser.parse_args()
    state_bool = args.state == "true"

    original_sources: Dict[Path, str] = {}

    mutated_count = 0
    unchanged_count = 0
    failed_count = 0

    if args.file:
        if not args.file.is_file():
            sys.exit(f"Error: {args.file} is not a valid file.")

        if args.file.suffix != ".py":
            sys.exit(f"Error: {args.file} is not a Python file.")

        outcome = process_file(args.file, args.flag, state_bool)

        if outcome.result == ProcessResult.MUTATED:
            mutated_count += 1
            if outcome.original_source is not None:
                original_sources[args.file] = outcome.original_source

        elif outcome.result == ProcessResult.UNCHANGED:
            unchanged_count += 1

        elif outcome.result == ProcessResult.FAILED:
            failed_count += 1

    elif args.dir:
        if not args.dir.is_dir():
            sys.exit(f"Error: {args.dir} is not a valid directory.")

        print(f"Scanning directory: {args.dir}...")

        scanner = RepoScanner(args.dir)

        for py_file in scanner.get_python_files():
            outcome = process_file(py_file, args.flag, state_bool)

            if outcome.result == ProcessResult.MUTATED:
                mutated_count += 1
                if outcome.original_source is not None:
                    original_sources[py_file] = outcome.original_source

            elif outcome.result == ProcessResult.UNCHANGED:
                unchanged_count += 1

            elif outcome.result == ProcessResult.FAILED:
                failed_count += 1

    print(
        f"Mutation summary: mutated {mutated_count} files, "
        f"left {unchanged_count} unchanged, "
        f"failed on {failed_count} files."
    )

    if failed_count > 0:
        if original_sources:
            print("Processing failed. Rolling back changed files.")
            rollback_files(original_sources)

        sys.exit(1)

    if args.validate:
        if mutated_count == 0:
            print("No files were mutated. Skipping validation.")
            return

        validation_root = resolve_validation_root(
            file_path=args.file,
            dir_path=args.dir,
            repo_root=args.repo_root,
        )

        validation_passed = run_validation(
            repo_root=validation_root,
            timeout_seconds=args.timeout_seconds,
        )

        if not validation_passed:
            print("Rolling back changed files because validation failed.")
            rollback_files(original_sources)
            sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()