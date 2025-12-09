#!/usr/bin/env python3
"""
Test script to verify the Phase 3 verification logic.

Creates test files and simulates the verification process to ensure:
1. All files are found and verified
2. Hash lookups work correctly
3. Missing files are detected
4. No files are skipped
"""

import tempfile
import shutil
from pathlib import Path
from ltfs_tools import hash_file

def create_test_files(base_dir: Path, num_files: int = 10, size_mb: int = 1):
    """Create test files with random data."""
    files_created = []

    # Create some directory structure
    (base_dir / "subdir1").mkdir()
    (base_dir / "subdir2").mkdir()
    (base_dir / "subdir1" / "nested").mkdir()

    paths = [
        base_dir,
        base_dir / "subdir1",
        base_dir / "subdir2",
        base_dir / "subdir1" / "nested",
    ]

    for i in range(num_files):
        # Distribute files across directories
        parent = paths[i % len(paths)]
        file_path = parent / f"testfile_{i:03d}.bin"

        # Create file with random data
        size_bytes = size_mb * 1024 * 1024
        with open(file_path, 'wb') as f:
            # Write in chunks to handle large files
            chunk_size = 1024 * 1024  # 1MB chunks
            remaining = size_bytes
            while remaining > 0:
                write_size = min(chunk_size, remaining)
                f.write(b'\x00' * write_size)  # Use zeros for speed
                remaining -= write_size

        files_created.append(file_path)

    return files_created


def simulate_phase1_hashing(source: Path, excluded_patterns: list[str]) -> dict[str, str]:
    """Simulate Phase 1: Hash all source files."""
    source_hashes = {}

    print(f"Phase 1: Hashing files in {source}")
    for path in source.rglob("*"):
        if path.is_file():
            rel_path = str(path.relative_to(source))
            file_hash = hash_file(path)
            source_hashes[rel_path] = file_hash
            print(f"  Hashed: {rel_path} -> {file_hash[:16]}...")

    print(f"Phase 1 complete: {len(source_hashes)} files hashed")
    return source_hashes


def simulate_phase3_verification(destination: Path, source_hashes: dict[str, str]) -> dict:
    """Simulate Phase 3: Verify destination files (NEW IMPLEMENTATION)."""
    files_verified = 0
    files_failed = 0
    failed_files = []
    files_checked = set()

    print(f"\nPhase 3: Verifying files in {destination}")

    # Read files sequentially from destination (filesystem order)
    for path in destination.rglob("*"):
        if path.is_file():
            rel_path = str(path.relative_to(destination))
            files_checked.add(rel_path)

            # Skip files not in our hash dictionary
            if rel_path not in source_hashes:
                print(f"  WARNING: File not in source_hashes: {rel_path}")
                continue

            source_hash = source_hashes[rel_path]

            try:
                dest_hash = hash_file(path)

                if source_hash == dest_hash:
                    files_verified += 1
                    print(f"  ✓ Verified: {rel_path}")
                else:
                    files_failed += 1
                    failed_files.append(f"{rel_path} (hash mismatch)")
                    print(f"  ✗ MISMATCH: {rel_path}")
            except OSError as e:
                files_failed += 1
                failed_files.append(f"{rel_path} (read error: {e})")
                print(f"  ✗ ERROR: {rel_path}: {e}")

    # Check for missing files (in source_hashes but not on destination)
    dest_files = {
        str(p.relative_to(destination))
        for p in destination.rglob("*")
        if p.is_file()
    }

    for rel_path in source_hashes.keys():
        if rel_path not in dest_files:
            files_failed += 1
            failed_files.append(f"{rel_path} (missing)")
            print(f"  ✗ MISSING: {rel_path}")

    print(f"\nPhase 3 complete:")
    print(f"  Files in source_hashes: {len(source_hashes)}")
    print(f"  Files found on destination: {len(dest_files)}")
    print(f"  Files checked: {len(files_checked)}")
    print(f"  Files verified: {files_verified}")
    print(f"  Files failed: {files_failed}")

    return {
        "verified": files_verified,
        "failed": files_failed,
        "failed_files": failed_files,
        "files_in_source": len(source_hashes),
        "files_in_dest": len(dest_files),
        "files_checked": len(files_checked),
    }


def test_normal_case():
    """Test normal case: source and destination identical."""
    print("\n" + "="*80)
    print("TEST 1: Normal case (source == destination)")
    print("="*80)

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source"
        destination = Path(tmpdir) / "destination"
        source.mkdir()

        # Create test files
        print(f"\nCreating 10 test files of 1MB each...")
        files = create_test_files(source, num_files=10, size_mb=1)
        print(f"Created {len(files)} files")

        # Copy to destination
        shutil.copytree(source, destination, dirs_exist_ok=True)

        # Simulate Phase 1
        source_hashes = simulate_phase1_hashing(source, [])

        # Simulate Phase 3
        result = simulate_phase3_verification(destination, source_hashes)

        # Verify results
        print("\n--- VERIFICATION ---")
        assert result["files_in_source"] == 10, f"Expected 10 files in source, got {result['files_in_source']}"
        assert result["files_in_dest"] == 10, f"Expected 10 files in dest, got {result['files_in_dest']}"
        assert result["files_checked"] == 10, f"Expected 10 files checked, got {result['files_checked']}"
        assert result["verified"] == 10, f"Expected 10 files verified, got {result['verified']}"
        assert result["failed"] == 0, f"Expected 0 failures, got {result['failed']}"
        print("✓ TEST PASSED: All files verified correctly")


def test_missing_file():
    """Test case: one file missing from destination."""
    print("\n" + "="*80)
    print("TEST 2: Missing file on destination")
    print("="*80)

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source"
        destination = Path(tmpdir) / "destination"
        source.mkdir()

        # Create test files
        print(f"\nCreating 10 test files of 1MB each...")
        files = create_test_files(source, num_files=10, size_mb=1)

        # Copy to destination
        shutil.copytree(source, destination, dirs_exist_ok=True)

        # Remove one file from destination
        missing_file = destination / "subdir1" / "testfile_005.bin"
        if missing_file.exists():
            missing_file.unlink()
            print(f"Removed {missing_file.relative_to(destination)} from destination")
        else:
            # Fallback: remove any file
            for f in destination.rglob("*"):
                if f.is_file():
                    missing_file = f
                    f.unlink()
                    print(f"Removed {f.relative_to(destination)} from destination")
                    break

        # Simulate Phase 1
        source_hashes = simulate_phase1_hashing(source, [])

        # Simulate Phase 3
        result = simulate_phase3_verification(destination, source_hashes)

        # Verify results
        print("\n--- VERIFICATION ---")
        assert result["files_in_source"] == 10, f"Expected 10 files in source, got {result['files_in_source']}"
        assert result["files_in_dest"] == 9, f"Expected 9 files in dest, got {result['files_in_dest']}"
        assert result["files_checked"] == 9, f"Expected 9 files checked, got {result['files_checked']}"
        assert result["verified"] == 9, f"Expected 9 files verified, got {result['verified']}"
        assert result["failed"] == 1, f"Expected 1 failure, got {result['failed']}"
        assert "missing" in result["failed_files"][0].lower(), "Expected 'missing' in failure message"
        print("✓ TEST PASSED: Missing file detected correctly")


def test_corrupted_file():
    """Test case: one file corrupted (different hash)."""
    print("\n" + "="*80)
    print("TEST 3: Corrupted file (hash mismatch)")
    print("="*80)

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source"
        destination = Path(tmpdir) / "destination"
        source.mkdir()

        # Create test files
        print(f"\nCreating 10 test files of 1MB each...")
        files = create_test_files(source, num_files=10, size_mb=1)

        # Copy to destination
        shutil.copytree(source, destination, dirs_exist_ok=True)

        # Corrupt one file
        corrupt_file = destination / "subdir1" / "nested" / "testfile_003.bin"
        if not corrupt_file.exists():
            # Fallback: corrupt any file
            for f in destination.rglob("*"):
                if f.is_file():
                    corrupt_file = f
                    break

        with open(corrupt_file, 'r+b') as f:
            f.seek(1024)
            f.write(b'\xFF' * 1024)  # Corrupt some bytes
        print(f"Corrupted {corrupt_file.relative_to(destination)}")

        # Simulate Phase 1
        source_hashes = simulate_phase1_hashing(source, [])

        # Simulate Phase 3
        result = simulate_phase3_verification(destination, source_hashes)

        # Verify results
        print("\n--- VERIFICATION ---")
        assert result["files_in_source"] == 10, f"Expected 10 files in source, got {result['files_in_source']}"
        assert result["files_in_dest"] == 10, f"Expected 10 files in dest, got {result['files_in_dest']}"
        assert result["files_checked"] == 10, f"Expected 10 files checked, got {result['files_checked']}"
        assert result["verified"] == 9, f"Expected 9 files verified, got {result['verified']}"
        assert result["failed"] == 1, f"Expected 1 failure, got {result['failed']}"
        assert "mismatch" in result["failed_files"][0].lower(), "Expected 'mismatch' in failure message"
        print("✓ TEST PASSED: Corrupted file detected correctly")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("LTFS Transfer Verification Logic Test Suite")
    print("="*80)

    try:
        test_normal_case()
        test_missing_file()
        test_corrupted_file()

        print("\n" + "="*80)
        print("ALL TESTS PASSED ✓")
        print("="*80)
    except AssertionError as e:
        print("\n" + "="*80)
        print(f"TEST FAILED ✗: {e}")
        print("="*80)
        exit(1)
    except Exception as e:
        print("\n" + "="*80)
        print(f"ERROR: {e}")
        print("="*80)
        import traceback
        traceback.print_exc()
        exit(1)
