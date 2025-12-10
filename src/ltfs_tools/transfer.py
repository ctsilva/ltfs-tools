"""
File transfer operations with verification.
"""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .utils import normalize_path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
    DownloadColumn,
)

from .config import Config, get_config
from .hash import hash_file
from .mhl import MHL, CreatorInfo, HashEntry, TapeInfo

console = Console()


@dataclass
class TransferResult:
    """Result of a transfer operation."""

    source: Path
    destination: Path
    tape_name: str
    start_time: datetime
    end_time: datetime
    files_total: int = 0
    files_transferred: int = 0
    files_verified: int = 0
    files_failed: int = 0
    bytes_total: int = 0
    failed_files: list[str] = field(default_factory=list)
    mhl_path: Optional[Path] = None
    log_path: Optional[Path] = None
    catalog_path: Optional[Path] = None

    # Phase timing
    phase1_duration: float = 0.0  # Hash source
    phase2_duration: float = 0.0  # Transfer
    phase3_duration: float = 0.0  # Verify
    phase4_duration: float = 0.0  # MHL generation
    phase5_duration: float = 0.0  # Catalog update

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    @property
    def success(self) -> bool:
        return self.files_failed == 0

    @property
    def phase1_throughput(self) -> float:
        """Phase 1 throughput in MB/s."""
        total_mb = self.bytes_total / (1024 * 1024)
        return total_mb / self.phase1_duration if self.phase1_duration > 0 else 0

    @property
    def phase2_throughput(self) -> float:
        """Phase 2 throughput in MB/s."""
        total_mb = self.bytes_total / (1024 * 1024)
        return total_mb / self.phase2_duration if self.phase2_duration > 0 else 0

    @property
    def phase3_throughput(self) -> float:
        """Phase 3 throughput in MB/s."""
        total_mb = self.bytes_total / (1024 * 1024)
        return total_mb / self.phase3_duration if self.phase3_duration > 0 else 0


class TransferError(Exception):
    """Error during transfer operations."""

    pass


def find_long_filenames(source: Path, max_length: int = 250) -> list[Path]:
    """Find files with names exceeding max_length bytes."""
    long_names = []
    for path in source.rglob("*"):
        if path.is_file() and len(path.name.encode("utf-8")) > max_length:
            long_names.append(path)
    return long_names


def check_source(source: Path) -> tuple[int, int]:
    """
    Check source and count files/size.

    Returns:
        Tuple of (file_count, total_bytes)
    """
    file_count = 0
    total_size = 0

    for path in source.rglob("*"):
        if path.is_file():
            file_count += 1
            try:
                total_size += path.stat().st_size
            except OSError:
                pass

    return file_count, total_size


def transfer(
    source: Path,
    tape_name: Optional[str] = None,
    dry_run: bool = False,
    verify: bool = True,
    config: Optional[Config] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> TransferResult:
    """
    Transfer files to an LTFS tape with verification.

    Args:
        source: Source directory or file
        tape_name: Name for logs/MHL (default: derived from mount point)
        dry_run: If True, show what would be transferred without copying
        verify: If True, verify hashes after transfer
        config: Configuration to use
        progress_callback: Optional callback(phase, current, total)

    Returns:
        TransferResult with details of the operation

    Raises:
        TransferError: If transfer fails
    """
    if config is None:
        config = get_config()

    if not source.exists():
        raise TransferError(f"Source not found: {source}")

    if not config.is_mounted():
        raise TransferError(f"No tape mounted at {config.mount_point}")

    # Initialize
    config.init_dirs()
    tape_name = tape_name or config.mount_point.name
    source_name = source.name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_time = datetime.now(timezone.utc)

    # Setup result
    result = TransferResult(
        source=source,
        destination=config.mount_point / source_name,
        tape_name=tape_name,
        start_time=start_time,
        end_time=start_time,  # Will be updated
    )

    # Count files
    result.files_total, result.bytes_total = check_source(source)

    if dry_run:
        console.print(f"[bold]Dry run:[/bold] Would transfer {result.files_total} files")
        result.end_time = datetime.now(timezone.utc)
        return result

    # Phase 1: Hash source files
    console.print("[bold blue]Phase 1:[/bold blue] Hashing source files...")
    phase1_start = datetime.now(timezone.utc)
    # Store hash and file size together to avoid filesystem lookup issues with Unicode normalization
    # Key: normalized path (NFC), Value: (hash, file_size)
    source_hashes: dict[str, tuple[str, int]] = {}
    excluded_files: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        DownloadColumn(),
        TextColumn("•"),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Hashing", total=result.bytes_total)

        bytes_processed = 0
        for path in source.rglob("*"):
            if path.is_file():
                rel_path = path.relative_to(source)

                # Skip excluded files
                if _should_exclude(rel_path, config.excludes):
                    excluded_files.append(str(rel_path))
                    continue

                try:
                    file_size = path.stat().st_size
                    # Update description with current file
                    progress.update(task, description=f"Hashing: {str(rel_path)[:60]}")

                    file_hash = hash_file(path)
                    # Normalize path for consistent comparison with LTFS (which uses NFC)
                    # Store size alongside hash to avoid re-reading filesystem with wrong normalization
                    source_hashes[normalize_path(str(rel_path))] = (file_hash, file_size)

                    bytes_processed += file_size
                    progress.update(task, completed=bytes_processed)
                except OSError as e:
                    console.print(f"[yellow]Warning:[/yellow] Could not hash {rel_path}: {e}")

    # Report excluded files
    if excluded_files:
        console.print(f"[dim]Excluded {len(excluded_files)} files matching exclusion patterns[/dim]")

    phase1_end = datetime.now(timezone.utc)
    phase1_duration = (phase1_end - phase1_start).total_seconds()

    # Phase 2: Transfer files with rsync
    # Drop page cache to get accurate transfer timing (source files were just hashed)
    # Only on Linux - macOS doesn't support vm.drop_caches
    if config.platform.name == "linux":
        console.print("[dim]Clearing page cache before transfer...[/dim]")
        try:
            subprocess.run(["sync"], check=False)
            subprocess.run(
                ["sudo", "-n", "sysctl", "-w", "vm.drop_caches=3"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass  # Silently continue if cache clearing fails

    console.print("[bold blue]Phase 2:[/bold blue] Transferring files...")
    phase2_start = datetime.now(timezone.utc)

    exclude_args = []
    for pattern in config.excludes:
        exclude_args.extend(["--exclude", pattern])

    # Build rsync command with platform-appropriate progress flags
    # macOS system rsync (2.6.9) doesn't support --info=progress2 or --no-i-r
    # Linux rsync 3.1+ supports both
    rsync_cmd = [
        "rsync",
        *config.rsync_opts,
        *exclude_args,
        f"{source}/",
        f"{result.destination}/",
    ]

    # Add progress2 and no-i-r only on Linux (rsync 3.1+)
    if config.platform.name == "linux":
        rsync_cmd.insert(2, "--info=progress2")
        rsync_cmd.insert(3, "--no-i-r")

    # Prepare log file
    log_path = config.log_dir / f"transfer_{tape_name}_{source_name}_{timestamp}.log"

    try:
        # Run rsync with live output
        with open(log_path, "w") as log_file:
            log_file.write(f"LTFS Transfer Log\n")
            log_file.write(f"================\n")
            log_file.write(f"Source: {source}\n")
            log_file.write(f"Destination: {result.destination}\n")
            log_file.write(f"Tape: {tape_name}\n")
            log_file.write(f"Started: {start_time.isoformat()}\n")
            log_file.write(f"Files counted: {result.files_total}\n")
            log_file.write(f"Files to transfer: {len(source_hashes)}\n")
            if excluded_files:
                log_file.write(f"Files excluded: {len(excluded_files)}\n")
                log_file.write(f"\n--- Excluded files ---\n")
                for excluded in excluded_files:
                    log_file.write(f"  {excluded}\n")
            log_file.write(f"\n--- Phase 1 (Hash source) ---\n")
            log_file.write(f"Started: {phase1_start.isoformat()}\n")
            log_file.write(f"Finished: {phase1_end.isoformat()}\n")
            log_file.write(f"Duration: {phase1_duration:.2f}s\n")
            total_mb = result.bytes_total / (1024 * 1024)
            log_file.write(f"Throughput: {total_mb / phase1_duration:.1f} MB/s\n")
            log_file.write(f"\n--- Phase 2 (Transfer) ---\n")
            log_file.write(f"Started: {phase2_start.isoformat()}\n")
            log_file.write(f"\n--- rsync output ---\n")
            log_file.flush()

            # Run rsync and tee output to both console and log
            # Use binary mode to handle non-UTF-8 filenames gracefully
            process = subprocess.Popen(
                rsync_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )

            # Stream output line by line, handling encoding errors
            for raw_line in process.stdout:
                # Decode with error handling for non-UTF-8 filenames
                line = raw_line.decode("utf-8", errors="replace")
                console.print(line.rstrip())
                log_file.write(line)

            process.wait()

            if process.returncode != 0:
                console.print(f"[yellow]Warning:[/yellow] rsync returned {process.returncode}")

        result.log_path = log_path

    except FileNotFoundError:
        raise TransferError("rsync not found in PATH")

    phase2_end = datetime.now(timezone.utc)
    phase2_duration = (phase2_end - phase2_start).total_seconds()

    # Log Phase 2 completion
    with open(log_path, "a") as f:
        f.write(f"\n--- Phase 2 completed ---\n")
        f.write(f"Finished: {phase2_end.isoformat()}\n")
        f.write(f"Duration: {phase2_duration:.2f}s\n")
        total_mb = result.bytes_total / (1024 * 1024)
        f.write(f"Throughput: {total_mb / phase2_duration:.1f} MB/s\n")

    # Phase 3: Verify destination hashes
    if verify:
        # Drop page cache to ensure we read from tape, not cache
        # Only on Linux - macOS doesn't support vm.drop_caches
        if config.platform.name == "linux":
            console.print("[dim]Clearing page cache to force tape reads...[/dim]")
            try:
                # Sync to ensure all writes are flushed
                subprocess.run(["sync"], check=False)
                # Drop page cache (requires sudo or appropriate permissions)
                # This writes 3 to /proc/sys/vm/drop_caches which drops page cache, dentries, and inodes
                subprocess.run(
                    ["sudo", "-n", "sysctl", "-w", "vm.drop_caches=3"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                console.print("[dim]Page cache cleared[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning:[/yellow] Could not drop page cache: {e}")
                console.print("[yellow]Verification may read from cache instead of tape![/yellow]")

        phase3_start = datetime.now(timezone.utc)
        console.print("[bold blue]Phase 3:[/bold blue] Verifying destination files...")

        # Calculate total bytes for verification progress (using stored sizes)
        verify_bytes_total = sum(
            file_size for (file_hash, file_size) in source_hashes.values()
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            DownloadColumn(),
            TextColumn("•"),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Verifying", total=verify_bytes_total)

            bytes_verified = 0

            # Read files sequentially from tape (filesystem order = tape physical order)
            # Then look up expected hash from dictionary (fast memory lookup)
            for path in result.destination.rglob("*"):
                if path.is_file():
                    # Normalize path for comparison (LTFS uses NFC, source may use NFD)
                    rel_path = normalize_path(str(path.relative_to(result.destination)))

                    # Skip files not in our hash dictionary (shouldn't happen)
                    if rel_path not in source_hashes:
                        continue

                    source_hash, expected_size = source_hashes[rel_path]

                    # Update description with current file
                    progress.update(task, description=f"Verifying: {str(rel_path)[:60]}")

                    try:
                        dest_hash = hash_file(path)
                        file_size = path.stat().st_size

                        if source_hash == dest_hash:
                            result.files_verified += 1
                        else:
                            result.files_failed += 1
                            result.failed_files.append(f"{rel_path} (hash mismatch)")
                            console.print(f"[red]MISMATCH:[/red] {rel_path}")

                        bytes_verified += file_size
                        progress.update(task, completed=bytes_verified)
                    except OSError as e:
                        result.files_failed += 1
                        result.failed_files.append(f"{rel_path} (read error: {e})")

            # Check for missing files (in source_hashes but not on destination)
            # Normalize paths for comparison (LTFS uses NFC, source may use NFD)
            dest_files = {
                normalize_path(str(p.relative_to(result.destination)))
                for p in result.destination.rglob("*")
                if p.is_file()
            }
            for rel_path in source_hashes.keys():
                # rel_path is already normalized when stored in source_hashes
                if rel_path not in dest_files:
                    result.files_failed += 1
                    result.failed_files.append(f"{rel_path} (missing)")
                    console.print(f"[red]MISSING:[/red] {rel_path}")

        result.files_transferred = result.files_verified

        phase3_end = datetime.now(timezone.utc)
        phase3_duration = (phase3_end - phase3_start).total_seconds()

        # Log Phase 3 completion
        with open(log_path, "a") as f:
            f.write(f"\n--- Phase 3 (Verify) ---\n")
            f.write(f"Started: {phase3_start.isoformat()}\n")
            f.write(f"Finished: {phase3_end.isoformat()}\n")
            f.write(f"Duration: {phase3_duration:.2f}s\n")
            total_mb = result.bytes_total / (1024 * 1024)
            f.write(f"Throughput: {total_mb / phase3_duration:.1f} MB/s\n")
            f.write(f"Files verified: {result.files_verified}\n")
            f.write(f"Files failed: {result.files_failed}\n")
            if result.failed_files:
                f.write(f"\n--- Failed files ---\n")
                for failed in result.failed_files:
                    f.write(f"  {failed}\n")
    else:
        phase3_duration = 0.0

    # Phase 4: Generate MHL file
    console.print("[bold blue]Phase 4:[/bold blue] Generating MHL file...")
    phase4_start = datetime.now(timezone.utc)

    mhl = MHL(
        creator_info=CreatorInfo.default(),
        tape_info=TapeInfo(name=tape_name),
    )
    mhl.creator_info.start_date = start_time
    mhl.creator_info.finish_date = datetime.now(timezone.utc)

    for rel_path, (file_hash, file_size) in source_hashes.items():
        # Use tape file for mtime (normalized path works on LTFS)
        tape_file = result.destination / rel_path
        try:
            stat = tape_file.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            mtime = None

        mhl.add_hash(
            HashEntry(
                file=rel_path,
                size=file_size,
                xxhash64be=file_hash,
                last_modification_date=mtime,
                hash_date=datetime.now(timezone.utc),
            )
        )

    mhl_path = config.mhl_dir / f"{tape_name}_{source_name}_{timestamp}.mhl"
    mhl.save(mhl_path)
    result.mhl_path = mhl_path

    phase4_end = datetime.now(timezone.utc)
    phase4_duration = (phase4_end - phase4_start).total_seconds()

    # Phase 5: Update catalog (zero-byte files + SQLite database)
    console.print("[bold blue]Phase 5:[/bold blue] Updating catalog...")
    phase5_start = datetime.now(timezone.utc)

    # 5a: Create zero-byte catalog files (legacy/browsable format)
    catalog_tape_dir = config.catalog_dir / tape_name / source_name
    catalog_tape_dir.mkdir(parents=True, exist_ok=True)

    for rel_path in source_hashes.keys():
        # Use tape file for mtime (normalized path works on LTFS)
        tape_file = result.destination / rel_path
        catalog_file = catalog_tape_dir / rel_path

        catalog_file.parent.mkdir(parents=True, exist_ok=True)
        catalog_file.touch()

        # Preserve original timestamp from tape (LTFS preserves mtimes)
        try:
            stat = tape_file.stat()
            import os

            os.utime(catalog_file, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass

    result.catalog_path = catalog_tape_dir

    # 5b: Update SQLite catalog database
    try:
        from .catalog_db import CatalogDB

        db = CatalogDB(config=config)
        db.add_tape(name=tape_name)

        # Prepare file records: (path, size, mtime, xxhash)
        db_files = []
        for rel_path, (file_hash, file_size) in source_hashes.items():
            # Get mtime from tape file
            tape_file = result.destination / rel_path
            try:
                mtime = datetime.fromtimestamp(tape_file.stat().st_mtime, tz=timezone.utc)
            except OSError:
                mtime = None

            db_files.append((rel_path, file_size, mtime, file_hash))

        db.add_files(tape_name, db_files, archived_at=datetime.now(timezone.utc))
        console.print(f"[dim]Added {len(db_files)} files to catalog database[/dim]")
    except Exception as e:
        # Don't fail transfer if database update fails
        console.print(f"[yellow]Warning:[/yellow] Could not update catalog database: {e}")

    phase5_end = datetime.now(timezone.utc)
    phase5_duration = (phase5_end - phase5_start).total_seconds()

    # Finalize
    result.end_time = datetime.now(timezone.utc)

    # Store phase durations in result
    result.phase1_duration = phase1_duration
    result.phase2_duration = phase2_duration
    result.phase3_duration = phase3_duration
    result.phase4_duration = phase4_duration
    result.phase5_duration = phase5_duration

    # Calculate throughput for each phase
    total_mb = result.bytes_total / (1024 * 1024)
    phase1_throughput = total_mb / phase1_duration if phase1_duration > 0 else 0
    phase2_throughput = total_mb / phase2_duration if phase2_duration > 0 else 0
    phase3_throughput = total_mb / phase3_duration if phase3_duration > 0 else 0

    # Update log with final stats
    with open(log_path, "a") as f:
        f.write(f"\n--- Summary ---\n")
        f.write(f"Finished: {result.end_time.isoformat()}\n")
        f.write(f"Duration: {result.duration_seconds:.1f}s\n")
        f.write(f"Files counted: {result.files_total}\n")
        f.write(f"Files transferred: {len(source_hashes)}\n")
        if excluded_files:
            f.write(f"Files excluded: {len(excluded_files)}\n")
        f.write(f"Files verified: {result.files_verified}\n")
        f.write(f"Files failed: {result.files_failed}\n")
        f.write(f"\n--- Phase Performance ---\n")
        f.write(f"Phase 1 (Hash source):    {phase1_duration:8.2f}s  ({phase1_throughput:6.1f} MB/s)\n")
        f.write(f"Phase 2 (Transfer):       {phase2_duration:8.2f}s  ({phase2_throughput:6.1f} MB/s)\n")
        if verify:
            f.write(f"Phase 3 (Verify):         {phase3_duration:8.2f}s  ({phase3_throughput:6.1f} MB/s)\n")
        f.write(f"Phase 4 (MHL):            {phase4_duration:8.2f}s\n")
        f.write(f"Phase 5 (Catalog):        {phase5_duration:8.2f}s\n")
        f.write(f"Overall throughput:       {total_mb / result.duration_seconds:6.1f} MB/s\n")

    return result


def _should_exclude(path: Path, patterns: list[str]) -> bool:
    """Check if a path matches any exclude pattern.

    Patterns:
    - Exact match: checks if pattern appears in any path component
    - Full-path glob (contains /): uses fnmatch on entire path
    - Glob patterns (with * or ?): uses fnmatch on each path component
    - Directory patterns (ending with /): matches directory names
    """
    import fnmatch

    path_str = str(path)
    path_parts = path.parts

    for pattern in patterns:
        # Directory pattern (ends with /)
        if pattern.endswith("/"):
            dir_pattern = pattern[:-1]
            for part in path_parts:
                if part == dir_pattern or fnmatch.fnmatch(part, dir_pattern):
                    return True
        # Full-path glob pattern (contains / and wildcards)
        elif ("*" in pattern or "?" in pattern) and "/" in pattern:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        # Component glob pattern (contains * or ?)
        elif "*" in pattern or "?" in pattern:
            for part in path_parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
        # Exact substring match
        elif pattern in path_str:
            return True

    return False
