#!/usr/bin/env python3
"""
Benchmark script for LTFS transfer and verification performance.

Tests different file size distributions:
- Many small files (1 GB total, 1 MB each = 1024 files)
- Medium files (1 GB total, 10 MB each = ~102 files)
- Few large files (1 GB total, 100 MB each = ~10 files)

Measures:
- Phase 1 (Hashing source): Time and throughput
- Phase 2 (rsync transfer): Time and throughput
- Phase 3 (Verification): Time and throughput
- Total time and overall throughput
"""

import tempfile
import shutil
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List
from ltfs_tools import hash_file

@dataclass
class BenchmarkResult:
    """Results from a single benchmark run."""
    scenario: str
    file_count: int
    file_size_mb: int
    total_size_gb: float

    # Phase timings
    phase1_time: float  # Hashing source
    phase2_time: float  # Transfer (rsync)
    phase3_time: float  # Verification
    total_time: float

    # Throughput (MB/s)
    phase1_throughput: float
    phase2_throughput: float
    phase3_throughput: float
    overall_throughput: float


def create_test_files(base_dir: Path, file_size_mb: int, total_size_gb: float) -> List[Path]:
    """
    Create test files for benchmarking.

    Args:
        base_dir: Directory to create files in
        file_size_mb: Size of each file in MB
        total_size_gb: Total size of all files in GB

    Returns:
        List of created file paths
    """
    file_size_bytes = file_size_mb * 1024 * 1024
    total_bytes = int(total_size_gb * 1024 * 1024 * 1024)
    num_files = total_bytes // file_size_bytes

    print(f"  Creating {num_files} files of {file_size_mb} MB each...")

    files_created = []

    # Create some directory structure
    dirs = [
        base_dir,
        base_dir / "subdir1",
        base_dir / "subdir2",
        base_dir / "subdir1" / "nested",
    ]

    for d in dirs[1:]:
        d.mkdir(parents=True, exist_ok=True)

    # Create files distributed across directories
    for i in range(num_files):
        parent = dirs[i % len(dirs)]
        file_path = parent / f"file_{i:04d}.bin"

        # Write file with zero bytes (fast)
        with open(file_path, 'wb') as f:
            # Write in chunks for large files
            chunk_size = 1024 * 1024  # 1MB chunks
            remaining = file_size_bytes
            while remaining > 0:
                write_size = min(chunk_size, remaining)
                f.write(b'\x00' * write_size)
                remaining -= write_size

        files_created.append(file_path)

    print(f"  Created {len(files_created)} files ({total_size_gb} GB total)")
    return files_created


def benchmark_phase1_hashing(source: Path) -> tuple[dict[str, str], float]:
    """Benchmark Phase 1: Hash all source files."""
    print("  Phase 1: Hashing source files...")

    source_hashes = {}
    start_time = time.time()

    for path in source.rglob("*"):
        if path.is_file():
            rel_path = str(path.relative_to(source))
            file_hash = hash_file(path)
            source_hashes[rel_path] = file_hash

    elapsed = time.time() - start_time
    print(f"    Completed in {elapsed:.2f}s")

    return source_hashes, elapsed


def benchmark_phase2_transfer(source: Path, destination: Path) -> float:
    """Benchmark Phase 2: Transfer files (using shutil.copytree for benchmark)."""
    print("  Phase 2: Transferring files...")

    start_time = time.time()
    shutil.copytree(source, destination, dirs_exist_ok=True)
    elapsed = time.time() - start_time

    print(f"    Completed in {elapsed:.2f}s")
    return elapsed


def benchmark_phase3_verification(destination: Path, source_hashes: dict[str, str]) -> float:
    """Benchmark Phase 3: Verify destination files (NEW IMPLEMENTATION)."""
    print("  Phase 3: Verifying destination files...")

    files_verified = 0
    files_failed = 0

    start_time = time.time()

    # Read files sequentially from destination (filesystem order)
    for path in destination.rglob("*"):
        if path.is_file():
            rel_path = str(path.relative_to(destination))

            # Skip files not in our hash dictionary
            if rel_path not in source_hashes:
                continue

            source_hash = source_hashes[rel_path]

            try:
                dest_hash = hash_file(path)

                if source_hash == dest_hash:
                    files_verified += 1
                else:
                    files_failed += 1
            except OSError:
                files_failed += 1

    # Check for missing files
    dest_files = {
        str(p.relative_to(destination))
        for p in destination.rglob("*")
        if p.is_file()
    }

    for rel_path in source_hashes.keys():
        if rel_path not in dest_files:
            files_failed += 1

    elapsed = time.time() - start_time

    print(f"    Completed in {elapsed:.2f}s")
    print(f"    Verified: {files_verified}, Failed: {files_failed}")

    return elapsed


def run_benchmark(scenario: str, file_size_mb: int, total_size_gb: float) -> BenchmarkResult:
    """Run a complete benchmark for a specific scenario."""
    print(f"\n{'='*80}")
    print(f"Benchmark: {scenario}")
    print(f"  File size: {file_size_mb} MB")
    print(f"  Total size: {total_size_gb} GB")
    print(f"{'='*80}")

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source"
        destination = Path(tmpdir) / "destination"
        source.mkdir()

        # Create test files
        files = create_test_files(source, file_size_mb, total_size_gb)
        file_count = len(files)

        # Calculate total size in bytes
        total_bytes = sum(f.stat().st_size for f in files)
        total_mb = total_bytes / (1024 * 1024)

        # Phase 1: Hash source files
        source_hashes, phase1_time = benchmark_phase1_hashing(source)
        phase1_throughput = total_mb / phase1_time if phase1_time > 0 else 0

        # Phase 2: Transfer files
        phase2_time = benchmark_phase2_transfer(source, destination)
        phase2_throughput = total_mb / phase2_time if phase2_time > 0 else 0

        # Phase 3: Verify destination
        phase3_time = benchmark_phase3_verification(destination, source_hashes)
        phase3_throughput = total_mb / phase3_time if phase3_time > 0 else 0

        # Total
        total_time = phase1_time + phase2_time + phase3_time
        overall_throughput = total_mb / total_time if total_time > 0 else 0

        return BenchmarkResult(
            scenario=scenario,
            file_count=file_count,
            file_size_mb=file_size_mb,
            total_size_gb=total_size_gb,
            phase1_time=phase1_time,
            phase2_time=phase2_time,
            phase3_time=phase3_time,
            total_time=total_time,
            phase1_throughput=phase1_throughput,
            phase2_throughput=phase2_throughput,
            phase3_throughput=phase3_throughput,
            overall_throughput=overall_throughput,
        )


def print_results(results: List[BenchmarkResult]):
    """Print benchmark results in a formatted table."""
    print("\n" + "="*80)
    print("BENCHMARK RESULTS SUMMARY")
    print("="*80)

    # Header
    print(f"\n{'Scenario':<30} {'Files':>8} {'Size/File':>10} {'Total':>8}")
    print("-" * 80)

    for r in results:
        print(f"{r.scenario:<30} {r.file_count:>8} {r.file_size_mb:>9} MB {r.total_size_gb:>7.1f} GB")

    # Timing table
    print(f"\n{'Scenario':<30} {'Phase 1':>10} {'Phase 2':>10} {'Phase 3':>10} {'Total':>10}")
    print(f"{'':30} {'(Hash)':>10} {'(Transfer)':>10} {'(Verify)':>10} {'':>10}")
    print("-" * 80)

    for r in results:
        print(f"{r.scenario:<30} {r.phase1_time:>9.2f}s {r.phase2_time:>9.2f}s {r.phase3_time:>9.2f}s {r.total_time:>9.2f}s")

    # Throughput table
    print(f"\n{'Scenario':<30} {'Phase 1':>10} {'Phase 2':>10} {'Phase 3':>10} {'Overall':>10}")
    print(f"{'':30} {'(Hash)':>10} {'(Transfer)':>10} {'(Verify)':>10} {'':>10}")
    print("-" * 80)

    for r in results:
        print(f"{r.scenario:<30} {r.phase1_throughput:>8.1f} MB/s {r.phase2_throughput:>8.1f} MB/s {r.phase3_throughput:>8.1f} MB/s {r.overall_throughput:>8.1f} MB/s")

    # Analysis
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)

    for r in results:
        print(f"\n{r.scenario}:")
        print(f"  Files: {r.file_count} x {r.file_size_mb} MB")
        print(f"  Phase 1 (Hashing):    {r.phase1_time:6.2f}s ({r.phase1_throughput:6.1f} MB/s) - {r.phase1_time/r.total_time*100:5.1f}% of total")
        print(f"  Phase 2 (Transfer):   {r.phase2_time:6.2f}s ({r.phase2_throughput:6.1f} MB/s) - {r.phase2_time/r.total_time*100:5.1f}% of total")
        print(f"  Phase 3 (Verify):     {r.phase3_time:6.2f}s ({r.phase3_throughput:6.1f} MB/s) - {r.phase3_time/r.total_time*100:5.1f}% of total")
        print(f"  Total:                {r.total_time:6.2f}s ({r.overall_throughput:6.1f} MB/s)")


def main():
    """Run all benchmarks."""
    print("="*80)
    print("LTFS Transfer & Verification Benchmark")
    print("="*80)
    print("\nThis benchmark tests the performance of:")
    print("  - Phase 1: Hashing source files (XXHash64)")
    print("  - Phase 2: Transferring files (copytree)")
    print("  - Phase 3: Verifying destination files (sequential read)")
    print("\nNote: Uses tmpfs (/tmp) for I/O, so results show CPU/algorithm performance")
    print("      Real tape performance will be slower (limited by tape I/O speed)")

    results = []

    # Benchmark 1: Many small files (1 MB each)
    results.append(run_benchmark(
        scenario="Many small files",
        file_size_mb=1,
        total_size_gb=1.0
    ))

    # Benchmark 2: Medium files (10 MB each)
    results.append(run_benchmark(
        scenario="Medium files",
        file_size_mb=10,
        total_size_gb=1.0
    ))

    # Benchmark 3: Large files (100 MB each)
    results.append(run_benchmark(
        scenario="Large files",
        file_size_mb=100,
        total_size_gb=1.0
    ))

    # Print summary
    print_results(results)

    print("\n" + "="*80)
    print("BENCHMARK COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
