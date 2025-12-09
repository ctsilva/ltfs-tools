"""
Catalog management for offline tape browsing.

Catalogs are zero-byte placeholder files that mirror the tape structure,
preserving timestamps. This allows browsing tape contents without mounting.
"""

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .config import Config, get_config
from .ltfs_index import LTFSIndexParser, LTFSIndex, IndexFile, IndexDirectory
from .utils import normalize_path


@dataclass
class CatalogEntry:
    """A file entry in a catalog."""

    relative_path: str
    size: int
    mtime: datetime

    @classmethod
    def from_path(cls, path: Path, base_path: Path) -> "CatalogEntry":
        """Create entry from actual file."""
        stat = path.stat()
        return cls(
            relative_path=str(path.relative_to(base_path)),
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime),
        )


def create_catalog(
    source: Path,
    tape_name: str,
    config: Optional[Config] = None,
) -> Path:
    """
    Create a catalog from a source directory.

    Creates zero-byte placeholder files mirroring the source structure.

    Args:
        source: Source directory to catalog
        tape_name: Name of the tape

    Returns:
        Path to catalog directory
    """
    if config is None:
        config = get_config()

    config.init_dirs()

    catalog_dir = config.catalog_dir / tape_name / source.name
    catalog_dir.mkdir(parents=True, exist_ok=True)

    for path in source.rglob("*"):
        if path.is_file():
            rel_path = path.relative_to(source)
            catalog_file = catalog_dir / rel_path

            # Create parent directories
            catalog_file.parent.mkdir(parents=True, exist_ok=True)

            # Create zero-byte file
            catalog_file.touch()

            # Preserve timestamp
            try:
                stat = path.stat()
                os.utime(catalog_file, (stat.st_atime, stat.st_mtime))
            except OSError:
                pass

    return catalog_dir


def create_catalog_snapshot(
    tape_name: str,
    config: Optional[Config] = None,
) -> Path:
    """
    Create a timestamped snapshot of the current catalog.

    Args:
        tape_name: Name of the tape

    Returns:
        Path to snapshot directory
    """
    if config is None:
        config = get_config()

    catalog_dir = config.catalog_dir / tape_name
    if not catalog_dir.exists():
        raise ValueError(f"No catalog found for tape: {tape_name}")

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    history_dir = config.catalog_dir / f"{tape_name}.history"
    snapshot_dir = history_dir / f"{tape_name}.{timestamp}"

    shutil.copytree(catalog_dir, snapshot_dir)

    return snapshot_dir


def list_catalog(
    tape_name: str,
    config: Optional[Config] = None,
) -> Iterator[CatalogEntry]:
    """
    List files in a catalog.

    Args:
        tape_name: Name of the tape

    Yields:
        CatalogEntry for each file
    """
    if config is None:
        config = get_config()

    catalog_dir = config.catalog_dir / tape_name
    if not catalog_dir.exists():
        return

    for path in catalog_dir.rglob("*"):
        if path.is_file():
            stat = path.stat()
            yield CatalogEntry(
                relative_path=str(path.relative_to(catalog_dir)),
                size=0,  # Catalog files are zero-byte placeholders
                mtime=datetime.fromtimestamp(stat.st_mtime),
            )


def list_tapes(config: Optional[Config] = None) -> list[str]:
    """
    List all tapes with catalogs.

    Returns:
        List of tape names
    """
    if config is None:
        config = get_config()

    if not config.catalog_dir.exists():
        return []

    tapes = []
    for item in config.catalog_dir.iterdir():
        if item.is_dir() and not item.name.endswith(".history"):
            tapes.append(item.name)

    return sorted(tapes)


def search_catalogs(
    pattern: str,
    tape_name: Optional[str] = None,
    config: Optional[Config] = None,
) -> list[tuple[str, str]]:
    """
    Search for files across catalogs.

    Args:
        pattern: Search pattern (supports * wildcards)
        tape_name: Optional tape to limit search to

    Returns:
        List of (tape_name, relative_path) tuples
    """
    if config is None:
        config = get_config()

    results = []

    if tape_name:
        tapes = [tape_name]
    else:
        tapes = list_tapes(config)

    # Convert simple wildcard to glob pattern
    import fnmatch

    # Normalize pattern for cross-platform consistency
    pattern = normalize_path(pattern)

    for tape in tapes:
        catalog_dir = config.catalog_dir / tape

        for path in catalog_dir.rglob("*"):
            if path.is_file():
                # Normalize path for cross-platform consistency
                rel_path = normalize_path(str(path.relative_to(catalog_dir)))
                if fnmatch.fnmatch(rel_path.lower(), pattern.lower()):
                    results.append((tape, rel_path))

    return results


def create_catalog_from_index(
    index_file: Path,
    tape_name: Optional[str] = None,
    config: Optional[Config] = None,
) -> Path:
    """
    Create a catalog from an LTFS index XML file.

    This is the key function that replicates Canister's catalog functionality.
    It parses the LTFS index and creates zero-byte placeholder files.

    Args:
        index_file: Path to LTFS index XML file
        tape_name: Optional tape name (extracted from index if not provided)
        config: Configuration

    Returns:
        Path to catalog directory
    """
    if config is None:
        config = get_config()

    config.init_dirs()

    # Parse LTFS index
    index = LTFSIndexParser.parse(index_file)

    # Use volume UUID as tape name if not provided
    if tape_name is None:
        tape_name = index.volume_uuid[:8]  # First 8 chars of UUID

    catalog_dir = config.catalog_dir / tape_name
    catalog_dir.mkdir(parents=True, exist_ok=True)

    # Create zero-byte files for entire directory tree
    def create_directory_catalog(directory: IndexDirectory, base_path: Path):
        """Recursively create catalog for directory."""
        # Create directory
        if directory.path != '/':
            dir_path = base_path / directory.path.lstrip('/')
            dir_path.mkdir(parents=True, exist_ok=True)

            # Set directory timestamp if available
            if directory.modify_time:
                try:
                    mtime = directory.modify_time.timestamp()
                    os.utime(dir_path, (mtime, mtime))
                except (OSError, ValueError):
                    pass

        # Create files in this directory
        for file in directory.files:
            file_path = base_path / file.path.lstrip('/')

            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Create zero-byte placeholder
            file_path.touch()

            # Set timestamp if available
            if file.modify_time:
                try:
                    mtime = file.modify_time.timestamp()
                    os.utime(file_path, (mtime, mtime))
                except (OSError, ValueError):
                    pass

        # Process subdirectories
        for subdir in directory.subdirs:
            create_directory_catalog(subdir, base_path)

    create_directory_catalog(index.root, catalog_dir)

    return catalog_dir


def update_catalog_from_latest_index(
    tape_name: str,
    config: Optional[Config] = None,
) -> Optional[Path]:
    """
    Update catalog from the latest LTFS index file.

    Searches for the most recent index file for the given tape
    and updates the catalog.

    Args:
        tape_name: Name/UUID of the tape
        config: Configuration

    Returns:
        Path to catalog directory, or None if no index found
    """
    if config is None:
        config = get_config()

    # Find latest index file
    index_files = list(config.index_dir.glob(f"{tape_name}*.xml"))
    if not index_files:
        # Try matching by UUID prefix
        index_files = list(config.index_dir.glob("*.xml"))
        index_files = [f for f in index_files if tape_name in f.name]

    if not index_files:
        return None

    # Sort by modification time to get latest
    latest_index = max(index_files, key=lambda p: p.stat().st_mtime)

    return create_catalog_from_index(latest_index, tape_name, config)


def get_catalog_stats(
    tape_name: str,
    config: Optional[Config] = None,
) -> dict:
    """
    Get statistics about a catalog.

    Returns:
        Dictionary with file_count, dir_count, oldest_file, newest_file
    """
    if config is None:
        config = get_config()

    catalog_dir = config.catalog_dir / tape_name
    if not catalog_dir.exists():
        return {"exists": False}

    file_count = 0
    dir_count = 0
    oldest_mtime = None
    newest_mtime = None

    for path in catalog_dir.rglob("*"):
        if path.is_file():
            file_count += 1
            mtime = path.stat().st_mtime
            if oldest_mtime is None or mtime < oldest_mtime:
                oldest_mtime = mtime
            if newest_mtime is None or mtime > newest_mtime:
                newest_mtime = mtime
        elif path.is_dir():
            dir_count += 1

    return {
        "exists": True,
        "file_count": file_count,
        "dir_count": dir_count,
        "oldest_file": datetime.fromtimestamp(oldest_mtime) if oldest_mtime else None,
        "newest_file": datetime.fromtimestamp(newest_mtime) if newest_mtime else None,
    }
