import argparse
import sys
from pathlib import Path

# Import your V0 brain
from project.core.mutator import apply_mutation

def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic Feature Flag Remover")
    parser.add_argument("--file", type=Path, required=True, help="Target Python file to mutate")
    parser.add_argument("--flag", type=str, required=True, help="Exact flag variable name")
    parser.add_argument("--state", type=str, choices=['true', 'false'], required=True, help="Target state of the flag")
    
    args = parser.parse_args()
    
    # Defensive check: Does the file exist?
    if not args.file.exists():
        sys.exit(f"Error: {args.file} does not exist.")
        
    state_bool = args.state == 'true'
    
    # Read the original file
    original_source = args.file.read_text(encoding="utf-8")
    
    try:
        # Pass it to V0
        new_source = apply_mutation(original_source, args.flag, state_bool)
        
        # Overwrite the file on disk
        args.file.write_text(new_source, encoding="utf-8")
        print(f"Success: Mutated {args.file}")
    except Exception as e:
        sys.exit(f"Transformation failed: {str(e)}")

if __name__ == "__main__":
    main()