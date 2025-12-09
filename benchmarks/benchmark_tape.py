#!/usr/bin/env python3
"""
Realistic LTFS tape benchmark - runs on actual mounted tape.

This benchmark writes and verifies files directly to tape to measure
real-world performance, not CPU/algorithm speed.

Usage:
    python benchmark_tape.py /media/tape 1  # 1 GB benchmark
    python benchmark_tape.py /media/tape 5  # 5 GB benchmark
"""

import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List
from ltfs_tools import hash_file


@dataclass
class BenchmarkResult:
    """Results from a tape benchmark run."""
    scenario: str
    file_count: int
    file_size_mb: int
    total_size_gb: float

    # Phase timings
    phase1_time: float  # Hashing source
    phase2_time: float  # Transfer (write to tape)
    phase3_time: float  # Verification (read from tape)
    total_time: float

    # Throughput (MB/s)
    phase1_throughput: float
    phase2_throughput: float
    phase3_throughput: float
    overall_throughput: float


def create_test_files(base_dir: Path, file_size_mb: int, total_size_gb: float) -> List[Path]:
    """Create test files."""
    file_size_bytes = file_size_mb * 1024 * 1024
    total_bytes = int(total_size_gb * 1024 * 1024 * 1024)
    num_files = total_bytes // file_size_bytes

    print(f"  Creating {num_files} files of {file_size_mb} MB each...")

    files_created = []
    base_dir.mkdir(parents=True, exist_ok=True)

    # Create files
    for i in range(num_files):
        file_path = base_dir / f"benchmark_file_{i:04d}.bin"

        # Write file with pseudo-random pattern (compressible for tape)
        with open(file_path, 'wb') as f:
            chunk_size = 1024 * 1024  # 1MB chunks
            remaining = file_size_bytes
            counter = 0
            while remaining > 0:
                write_size = min(chunk_size, remaining)
                # Simple pattern that's somewhat compressible
                data = bytes([counter % 256]) * write_size
                f.write(data)
                remaining -= write_size
                counter += 1

        files_created.append(file_path)

    print(f"  Created {len(files_created)} files ({total_size_gb:.2f} GB total)")
    return files_created


def benchmark_phase1_hashing(source_dir: Path) -> tuple[dict[str, str], float]:
    """Benchmark Phase 1: Hash all source files."""
    print("  Phase 1: Hashing source files...")

    source_hashes = {}
    start_time = time.time()

    for path in sorted(source_dir.glob("*.bin")):
        if path.is_file():
            rel_path = path.name
            file_hash = hash_file(path)
            source_hashes[rel_path] = file_hash

    elapsed = time.time() - start_time
    print(f"    Completed in {elapsed:.2f}s")

    return source_hashes, elapsed


def benchmark_phase2_transfer(source_dir: Path, dest_dir: Path) -> float:
    """Benchmark Phase 2: Transfer files to tape."""
    print("  Phase 2: Writing to tape...")

    dest_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    for source_file in sorted(source_dir.glob("*.bin")):
        dest_file = dest_dir / source_file.name

        # Copy file
        with open(source_file, 'rb') as src, open(dest_file, 'wb') as dst:
            while True:
                chunk = src.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                dst.write(chunk)

    # Sync to ensure data is written
    import subprocess
    subprocess.run(['sync'], check=False)

    elapsed = time.time() - start_time
    print(f"    Completed in {elapsed:.2f}s")

    return elapsed


def benchmark_phase3_verification(dest_dir: Path, source_hashes: dict[str, str]) -> float:
    """Benchmark Phase 3: Verify files on tape (sequential read)."""
    print("  Phase 3: Verifying from tape...")

    files_verified = 0
    files_failed = 0

    start_time = time.time()

    # Read files sequentially from tape
    for path in sorted(dest_dir.glob("*.bin")):
        if path.is_file():
            rel_path = path.name

            if rel_path not in source_hashes:
                continue

            source_hash = source_hashes[rel_path]

            try:
                dest_hash = hash_file(path)

                if source_hash == dest_hash:
                    files_verified += 1
                else:
                    files_failed += 1
                    print(f"    ✗ MISMATCH: {rel_path}")
            except OSError as e:
                files_failed += 1
                print(f"    ✗ ERROR: {rel_path}: {e}")

    elapsed = time.time() - start_time

    print(f"    Completed in {elapsed:.2f}s")
    print(f"    Verified: {files_verified}, Failed: {files_failed}")

    return elapsed


def cleanup(source_dir: Path, dest_dir: Path):
    """Clean up test files."""
    print("  Cleaning up test files...")

    import shutil
    if source_dir.exists():
        shutil.rmtree(source_dir)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    print("  Cleanup complete")


def run_benchmark(
    tape_mount: Path,
    scenario: str,
    file_size_mb: int,
    total_size_gb: float
) -> BenchmarkResult:
    """Run a complete tape benchmark."""
    print(f"\n{'='*80}")
    print(f"Benchmark: {scenario}")
    print(f"  File size: {file_size_mb} MB")
    print(f"  Total size: {total_size_gb} GB")
    print(f"  Tape mount: {tape_mount}")
    print(f"{'='*80}")

    # Create directories
    source_dir = Path("/tmp/ltfs_benchmark_source")
    dest_dir = tape_mount / "ltfs_benchmark_dest"

    try:
        # Create test files in /tmp
        files = create_test_files(source_dir, file_size_mb, total_size_gb)
        file_count = len(files)

        # Calculate total size in bytes
        total_bytes = sum(f.stat().st_size for f in files)
        total_mb = total_bytes / (1024 * 1024)

        # Phase 1: Hash source files
        source_hashes, phase1_time = benchmark_phase1_hashing(source_dir)
        phase1_throughput = total_mb / phase1_time if phase1_time > 0 else 0

        # Phase 2: Transfer to tape
        phase2_time = benchmark_phase2_transfer(source_dir, dest_dir)
        phase2_throughput = total_mb / phase2_time if phase2_time > 0 else 0

        # Phase 3: Verify from tape
        phase3_time = benchmark_phase3_verification(dest_dir, source_hashes)
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
    finally:
        # Always cleanup
        cleanup(source_dir, dest_dir)


def print_result(result: BenchmarkResult):
    """Print a single benchmark result."""
    print(f"\n{'='*80}")
    print(f"RESULTS: {result.scenario}")
    print(f"{'='*80}")
    print(f"Files: {result.file_count} x {result.file_size_mb} MB = {result.total_size_gb:.2f} GB")
    print()
    print(f"Phase 1 (Hashing):    {result.phase1_time:8.2f}s  ({result.phase1_throughput:6.1f} MB/s)")
    print(f"Phase 2 (Write):      {result.phase2_time:8.2f}s  ({result.phase2_throughput:6.1f} MB/s)")
    print(f"Phase 3 (Verify):     {result.phase3_time:8.2f}s  ({result.phase3_throughput:6.1f} MB/s)")
    print(f"{'─'*80}")
    print(f"Total:                {result.total_time:8.2f}s  ({result.overall_throughput:6.1f} MB/s)")
    print()
    print(f"Phase breakdown:")
    print(f"  Phase 1: {result.phase1_time/result.total_time*100:5.1f}% of total time")
    print(f"  Phase 2: {result.phase2_time/result.total_time*100:5.1f}% of total time")
    print(f"  Phase 3: {result.phase3_time/result.total_time*100:5.1f}% of total time")


def main():
    """Run tape benchmark."""
    if len(sys.argv) < 2:
        print("Usage: python benchmark_tape.py <tape_mount_point> [size_gb]")
        print()
        print("Example:")
        print("  python benchmark_tape.py /media/tape 1    # 1 GB benchmark")
        print("  python benchmark_tape.py /media/tape 0.5  # 500 MB benchmark")
        sys.exit(1)

    tape_mount = Path(sys.argv[1])
    total_size_gb = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    if not tape_mount.exists():
        print(f"Error: Tape mount point does not exist: {tape_mount}")
        sys.exit(1)

    if not tape_mount.is_dir():
        print(f"Error: Not a directory: {tape_mount}")
        sys.exit(1)

    print("="*80)
    print("LTFS Tape Benchmark - Real Performance Test")
    print("="*80)
    print()
    print(f"Tape mount point: {tape_mount}")
    print(f"Total data size:  {total_size_gb} GB")
    print()
    print("WARNING: This benchmark will write data to the tape!")
    print()

    # Run benchmarks with different file sizes
    results = []

    # Small files (1 MB each)
    if total_size_gb >= 0.1:
        result = run_benchmark(
            tape_mount=tape_mount,
            scenario="Many small files (1 MB each)",
            file_size_mb=1,
            total_size_gb=min(0.1, total_size_gb)  # Cap at 100 MB for small files
        )
        results.append(result)
        print_result(result)

    # Medium files (10 MB each)
    if total_size_gb >= 0.5:
        result = run_benchmark(
            tape_mount=tape_mount,
            scenario="Medium files (10 MB each)",
            file_size_mb=10,
            total_size_gb=min(0.5, total_size_gb)  # Cap at 500 MB
        )
        results.append(result)
        print_result(result)

    # Large files (100 MB each)
    result = run_benchmark(
        tape_mount=tape_mount,
        scenario="Large files (100 MB each)",
        file_size_mb=100,
        total_size_gb=total_size_gb
    )
    results.append(result)
    print_result(result)

    # Summary
    if len(results) > 1:
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        for r in results:
            print(f"\n{r.scenario}:")
            print(f"  Write speed:  {r.phase2_throughput:6.1f} MB/s")
            print(f"  Verify speed: {r.phase3_throughput:6.1f} MB/s")


if __name__ == "__main__":
    main()
