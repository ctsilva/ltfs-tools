"""
Verification operations using MHL files.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from .config import Config, get_config
from .hash import hash_file
from .mhl import MHL

console = Console()


@dataclass
class VerifyResult:
    """Result of a verification operation."""

    mhl_path: Path
    base_path: Path
    total_files: int = 0
    verified: int = 0
    failed: int = 0
    missing: int = 0
    failed_files: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.failed == 0 and self.missing == 0


class VerifyError(Exception):
    """Error during verification."""

    pass


def verify(
    mhl_path: Path,
    base_path: Optional[Path] = None,
    config: Optional[Config] = None,
) -> VerifyResult:
    """
    Verify files against an MHL file.

    Args:
        mhl_path: Path to MHL file
        base_path: Base directory containing files (default: mount point)
        config: Configuration to use

    Returns:
        VerifyResult with verification details

    Raises:
        VerifyError: If verification cannot proceed
    """
    if config is None:
        config = get_config()

    if not mhl_path.exists():
        raise VerifyError(f"MHL file not found: {mhl_path}")

    base_path = base_path or config.mount_point

    if not base_path.exists():
        raise VerifyError(f"Base path not found: {base_path}")

    # Load MHL
    try:
        mhl = MHL.load(mhl_path)
    except Exception as e:
        raise VerifyError(f"Failed to parse MHL file: {e}")

    result = VerifyResult(
        mhl_path=mhl_path,
        base_path=base_path,
        total_files=len(mhl),
    )

    console.print(f"[bold]Verifying {result.total_files} files from MHL...[/bold]")
    console.print(f"MHL: {mhl_path}")
    console.print(f"Base path: {base_path}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Verifying", total=result.total_files)

        for entry in mhl:
            file_path = base_path / entry.file

            if not file_path.exists():
                result.missing += 1
                result.missing_files.append(entry.file)
            else:
                try:
                    actual_hash = hash_file(file_path)

                    if actual_hash.lower() == entry.xxhash64be.lower():
                        result.verified += 1
                    else:
                        result.failed += 1
                        result.failed_files.append(
                            f"{entry.file} (expected: {entry.xxhash64be}, got: {actual_hash})"
                        )
                except OSError as e:
                    result.failed += 1
                    result.failed_files.append(f"{entry.file} (read error: {e})")

            progress.advance(task)

    return result


def verify_file(
    file_path: Path,
    expected_hash: str,
) -> bool:
    """
    Verify a single file against an expected hash.

    Args:
        file_path: Path to file
        expected_hash: Expected XXHash64 hex string

    Returns:
        True if hash matches
    """
    if not file_path.exists():
        return False

    actual_hash = hash_file(file_path)
    return actual_hash.lower() == expected_hash.lower()


def compare_mhl_files(mhl1_path: Path, mhl2_path: Path) -> dict:
    """
    Compare two MHL files.

    Args:
        mhl1_path: Path to first MHL file
        mhl2_path: Path to second MHL file

    Returns:
        Dictionary with comparison results:
        - 'common': Files in both with matching hashes
        - 'different': Files in both with different hashes
        - 'only_in_first': Files only in first MHL
        - 'only_in_second': Files only in second MHL
    """
    mhl1 = MHL.load(mhl1_path)
    mhl2 = MHL.load(mhl2_path)

    hashes1 = {entry.file: entry.xxhash64be for entry in mhl1}
    hashes2 = {entry.file: entry.xxhash64be for entry in mhl2}

    files1 = set(hashes1.keys())
    files2 = set(hashes2.keys())

    common_files = files1 & files2
    only_in_first = files1 - files2
    only_in_second = files2 - files1

    matching = []
    different = []

    for f in common_files:
        if hashes1[f].lower() == hashes2[f].lower():
            matching.append(f)
        else:
            different.append(f)

    return {
        "common": sorted(matching),
        "different": sorted(different),
        "only_in_first": sorted(only_in_first),
        "only_in_second": sorted(only_in_second),
    }
