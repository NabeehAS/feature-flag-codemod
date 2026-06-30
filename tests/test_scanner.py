from pathlib import Path
from project.discovery.scanner import RepoScanner

def test_repo_scanner_discovers_python_files(tmp_path: Path):
    # 1. Setup a fake repository structure
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").touch()
    (src_dir / "utils.py").touch()
    
    # 2. Setup folders that SHOULD be ignored
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    (venv_dir / "site_packages.py").touch() # This should NOT be found
    
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_main.py").touch() # This should NOT be found
    
    # 3. Setup a random non-python file
    (src_dir / "config.json").touch() # This should NOT be found

    # Execute
    scanner = RepoScanner(root_dir=tmp_path)
    found_files = list(scanner.get_python_files())

    # Assertions
    # We should only find the two files in the src folder
    assert len(found_files) == 2
    
    # Extract just the filenames to check them easily
    file_names = [f.name for f in found_files]
    assert "main.py" in file_names
    assert "utils.py" in file_names
    assert "site_packages.py" not in file_names
    assert "config.json" not in file_names