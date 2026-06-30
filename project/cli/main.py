import argparse
import sys
from pathlib import Path

# Import your V0 brain and V2 eyes
from project.core.mutator import apply_mutation
from project.discovery.scanner import RepoScanner

def process_file(file_path: Path, flag: str, state_bool: bool) -> bool:
    """Helper to process a single file. Returns True if mutated, False on error."""
    try:
        original_source = file_path.read_text(encoding="utf-8")
        new_source = apply_mutation(original_source, flag, state_bool)
        
        # Optimization: Only write to disk if the AST actually changed something
        if original_source != new_source:
            file_path.write_text(new_source, encoding="utf-8")
            print(f"[*] Mutated: {file_path}")
            return True
        return False
    except Exception as e:
        print(f"[!] Failed {file_path}: {str(e)}", file=sys.stderr)
        return False

def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic Feature Flag Remover")
    parser.add_argument("--flag", type=str, required=True, help="Exact flag variable name")
    parser.add_argument("--state", type=str, choices=['true', 'false'], required=True, help="Target state of the flag")
    
    # Require EITHER a file OR a directory, but not both
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=Path, help="Target a single Python file")
    group.add_argument("--dir", type=Path, help="Target an entire repository directory")
    
    args = parser.parse_args()
    state_bool = args.state == 'true'
    
    # Handle Single File
    if args.file:
        if not args.file.exists():
            sys.exit(f"Error: {args.file} does not exist.")
        process_file(args.file, args.flag, state_bool)
        print("Done.")
        
    # Handle Entire Directory
    elif args.dir:
        if not args.dir.is_dir():
            sys.exit(f"Error: {args.dir} is not a valid directory.")
            
        print(f"Scanning directory: {args.dir}...")
        scanner = RepoScanner(args.dir)
        
        processed_count = 0
        for py_file in scanner.get_python_files():
            if process_file(py_file, args.flag, state_bool):
                processed_count += 1
                
        print(f"Done. Successfully mutated {processed_count} files.")

if __name__ == "__main__":
    main()