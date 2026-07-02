import argparse
import sys
from enum import Enum
from pathlib import Path

# Import your V0 brain and V2 eyes
from project.core.mutator import apply_mutation
from project.discovery.scanner import RepoScanner


class ProcessResult(str, Enum):
    """Represents the outcome of processing a single file."""
    MUTATED = "mutated"
    UNCHANGED = "unchanged"
    FAILED = "failed"


def process_file(file_path: Path, flag: str, state_bool: bool) -> ProcessResult:
    """
    Process a single Python file.

    Returns:
        ProcessResult.MUTATED if the file was changed.
        ProcessResult.UNCHANGED if the flag was not found or no change was needed.
        ProcessResult.FAILED if parsing, reading, writing, or mutation failed.
    """
    try:
        original_source = file_path.read_text(encoding="utf-8")
        new_source = apply_mutation(original_source, flag, state_bool)

        # Optimization: only write to disk if the CST output actually changed something.
        if original_source != new_source:
            file_path.write_text(new_source, encoding="utf-8")
            print(f"[*] Mutated: {file_path}")
            return ProcessResult.MUTATED

        return ProcessResult.UNCHANGED

    except Exception as e:
        print(f"[!] Failed {file_path}: {str(e)}", file=sys.stderr)
        return ProcessResult.FAILED


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

    # Require EITHER a file OR a directory, but not both.
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

    # Handle single-file mode.
    if args.file:
        if not args.file.is_file():
            sys.exit(f"Error: {args.file} is not a valid file.")

        if args.file.suffix != ".py":
            sys.exit(f"Error: {args.file} is not a Python file.")

        result = process_file(args.file, args.flag, state_bool)

        if result == ProcessResult.FAILED:
            sys.exit(1)

        print("Done.")
        return

    # Handle repository-wide directory mode.
    if args.dir:
        if not args.dir.is_dir():
            sys.exit(f"Error: {args.dir} is not a valid directory.")

        print(f"Scanning directory: {args.dir}...")

        scanner = RepoScanner(args.dir)

        mutated_count = 0
        unchanged_count = 0
        failed_count = 0

        for py_file in scanner.get_python_files():
            result = process_file(py_file, args.flag, state_bool)

            if result == ProcessResult.MUTATED:
                mutated_count += 1
            elif result == ProcessResult.UNCHANGED:
                unchanged_count += 1
            elif result == ProcessResult.FAILED:
                failed_count += 1

        print(
            f"Done. Mutated {mutated_count} files, "
            f"left {unchanged_count} unchanged, "
            f"failed on {failed_count} files."
        )

        if failed_count > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()