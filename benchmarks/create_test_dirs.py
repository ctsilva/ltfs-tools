#!/usr/bin/env python3
"""
Create test directories for LTFS transfer benchmarking.

This script creates directories with different file size distributions,
each with a unique name to avoid clashes when writing to tape multiple times.

Usage:
    python create_test_dirs.py [--base-dir PATH] [--size-gb SIZE]

Examples:
    python create_test_dirs.py                           # Create all test dirs in /tmp
    python create_test_dirs.py --base-dir /data/tests    # Custom location
    python create_test_dirs.py --size-gb 0.5             # 500MB per scenario
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime


def create_test_files(base_dir: Path, file_size_mb: int, total_size_gb: float):
    """Create test files with pseudo-random data."""
    file_size_bytes = file_size_mb * 1024 * 1024
    total_bytes = int(total_size_gb * 1024 * 1024 * 1024)
    num_files = total_bytes // file_size_bytes

    print(f"  Creating {num_files} files of {file_size_mb} MB each...")

    base_dir.mkdir(parents=True, exist_ok=True)

    # Create files
    for i in range(num_files):
        file_path = base_dir / f"testfile_{i:04d}.bin"

        # Write file with pseudo-random pattern (compressible for tape)
        with open(file_path, 'wb') as f:
            chunk_size = 1024 * 1024  # 1MB chunks
            remaining = file_size_bytes
            counter = i  # Use file index as seed for variation
            while remaining > 0:
                write_size = min(chunk_size, remaining)
                # Simple pattern that varies per file but is compressible
                data = bytes([(counter + j) % 256 for j in range(write_size)])
                f.write(data)
                remaining -= write_size
                counter += 1

    actual_size_gb = (num_files * file_size_bytes) / (1024 * 1024 * 1024)
    print(f"  Created {num_files} files ({actual_size_gb:.2f} GB total)")
    return num_files, actual_size_gb


def create_test_scenarios(base_dir: Path, size_gb: float):
    """Create all test scenarios with unique names."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    scenarios = []

    # Scenario 1: Many small files (1 MB each)
    scenario_name = f"test-small-1mb-{timestamp}"
    scenario_dir = base_dir / scenario_name
    print(f"\n{'='*80}")
    print(f"Creating: {scenario_name}")
    print(f"  File size: 1 MB")
    print(f"  Target size: {size_gb} GB")
    print(f"{'='*80}")

    num_files, actual_size = create_test_files(scenario_dir, file_size_mb=1, total_size_gb=size_gb)
    scenarios.append({
        "name": scenario_name,
        "path": scenario_dir,
        "file_size_mb": 1,
        "num_files": num_files,
        "actual_size_gb": actual_size,
    })

    # Scenario 2: Medium files (10 MB each)
    scenario_name = f"test-medium-10mb-{timestamp}"
    scenario_dir = base_dir / scenario_name
    print(f"\n{'='*80}")
    print(f"Creating: {scenario_name}")
    print(f"  File size: 10 MB")
    print(f"  Target size: {size_gb} GB")
    print(f"{'='*80}")

    num_files, actual_size = create_test_files(scenario_dir, file_size_mb=10, total_size_gb=size_gb)
    scenarios.append({
        "name": scenario_name,
        "path": scenario_dir,
        "file_size_mb": 10,
        "num_files": num_files,
        "actual_size_gb": actual_size,
    })

    # Scenario 3: Large files (100 MB each)
    scenario_name = f"test-large-100mb-{timestamp}"
    scenario_dir = base_dir / scenario_name
    print(f"\n{'='*80}")
    print(f"Creating: {scenario_name}")
    print(f"  File size: 100 MB")
    print(f"  Target size: {size_gb} GB")
    print(f"{'='*80}")

    num_files, actual_size = create_test_files(scenario_dir, file_size_mb=100, total_size_gb=size_gb)
    scenarios.append({
        "name": scenario_name,
        "path": scenario_dir,
        "file_size_mb": 100,
        "num_files": num_files,
        "actual_size_gb": actual_size,
    })

    return scenarios


def print_summary(scenarios: list, base_dir: Path):
    """Print summary and transfer commands."""
    print("\n" + "="*80)
    print("TEST DIRECTORIES CREATED")
    print("="*80)
    print(f"\nBase directory: {base_dir}\n")

    total_size = sum(s["actual_size_gb"] for s in scenarios)

    print(f"{'Scenario':<30} {'Files':>8} {'Size/File':>10} {'Total':>10}")
    print("-" * 80)
    for s in scenarios:
        print(f"{s['name']:<30} {s['num_files']:>8} {s['file_size_mb']:>9} MB {s['actual_size_gb']:>9.2f} GB")
    print("-" * 80)
    print(f"{'TOTAL':<30} {'':>8} {'':>10} {total_size:>9.2f} GB")

    print("\n" + "="*80)
    print("TRANSFER COMMANDS")
    print("="*80)
    print("\nTo test with actual ltfs-tool transfer pipeline:\n")

    for s in scenarios:
        print(f"# {s['name']} ({s['num_files']} files x {s['file_size_mb']} MB)")
        print(f"ltfs-tool transfer {s['path']} {s['name']}")
        print()

    print("\nTo test all scenarios in sequence:")
    print("```bash")
    for s in scenarios:
        print(f"ltfs-tool transfer {s['path']} {s['name']}")
    print("```")

    print("\n" + "="*80)
    print("CLEANUP")
    print("="*80)
    print(f"\nTo remove test directories:")
    print(f"rm -rf {base_dir}/test-*-{scenarios[0]['name'].split('-')[-1]}")


def main():
    parser = argparse.ArgumentParser(
        description="Create test directories for LTFS transfer benchmarking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Create all test dirs in /tmp (10 GB each)
  %(prog)s --base-dir /data/tests    # Custom location
  %(prog)s --size-gb 0.5             # 500MB per scenario
  %(prog)s --size-gb 5               # 5GB per scenario
        """
    )

    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("/tmp"),
        help="Base directory for test files (default: /tmp)"
    )

    parser.add_argument(
        "--size-gb",
        type=float,
        default=10.0,
        help="Size of each test scenario in GB (default: 10.0)"
    )

    args = parser.parse_args()

    # Validate base directory
    if not args.base_dir.exists():
        print(f"Error: Base directory does not exist: {args.base_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.base_dir.is_dir():
        print(f"Error: Not a directory: {args.base_dir}", file=sys.stderr)
        sys.exit(1)

    if args.size_gb <= 0:
        print(f"Error: Size must be greater than 0", file=sys.stderr)
        sys.exit(1)

    print("="*80)
    print("LTFS Test Directory Generator")
    print("="*80)
    print(f"\nBase directory: {args.base_dir}")
    print(f"Size per scenario: {args.size_gb} GB")
    print(f"\nThis will create 3 test scenarios:")
    print(f"  1. Small files (1 MB each)")
    print(f"  2. Medium files (10 MB each)")
    print(f"  3. Large files (100 MB each)")
    print(f"\nTotal data to create: {args.size_gb * 3:.2f} GB")
    print(f"\nPress Ctrl+C to cancel...")

    import time
    time.sleep(2)

    # Create scenarios
    scenarios = create_test_scenarios(args.base_dir, args.size_gb)

    # Print summary
    print_summary(scenarios, args.base_dir)


if __name__ == "__main__":
    main()
