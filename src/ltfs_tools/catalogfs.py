"""
CatalogFS - FUSE filesystem for browsing LTFS tape catalogs.

Presents tape catalogs as a virtual filesystem with real file sizes
(from LTFS index files or SQLite database) without consuming disk space.

Inspired by Canister's catalog browsing feature on macOS.
"""

import errno
import os
import stat
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from fuse import FUSE, FuseOSError, Operations
    FUSE_AVAILABLE = True
except ImportError:
    FUSE_AVAILABLE = False
    # Stub classes for when fuse is not available
    class Operations:
        pass
    class FuseOSError(Exception):
        pass
    class FUSE:
        pass

from .ltfs_index import LTFSIndex, LTFSIndexParser, IndexFile, IndexDirectory


class CatalogFSFromDB(Operations):
    """
    FUSE filesystem that presents tape catalogs from SQLite database.

    Faster than XML-based CatalogFS for large catalogs, with the same
    directory structure.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize CatalogFS from SQLite database.

        Args:
            db_path: Path to catalog database (default: from config)
        """
        from .catalog_db import CatalogDB

        self.db = CatalogDB(db_path=db_path)
        self._mount_time = time.time()

        # Build path cache from database
        self._path_cache: Dict[str, Tuple[bool, int, float, str]] = {}
        self._tape_names: list[str] = []
        self._load_from_db()

    def _load_from_db(self):
        """Load file metadata from database into cache."""
        self._path_cache.clear()
        self._tape_names.clear()

        # Get all tapes
        tapes = self.db.list_tapes()
        self._tape_names = [t.name for t in tapes]

        # For each tape, get all files
        for tape in tapes:
            # Cache the tape root directory
            self._path_cache[f"/{tape.name}"] = (True, 0, self._mount_time, tape.name)

            # Get all files for this tape (use a large limit)
            results = self.db.search("*", tape_name=tape.name, limit=1000000)

            # Build directory set
            directories: set[str] = set()

            for r in results:
                file_path = f"/{tape.name}/{r.path}"
                mtime = r.mtime.timestamp() if r.mtime else self._mount_time

                # Cache file
                self._path_cache[file_path] = (False, r.size, mtime, tape.name)

                # Cache all parent directories
                parts = r.path.split("/")
                for i in range(1, len(parts)):
                    dir_path = f"/{tape.name}/{'/'.join(parts[:i])}"
                    if dir_path not in directories:
                        directories.add(dir_path)
                        self._path_cache[dir_path] = (True, 0, self._mount_time, tape.name)

    def _get_directory_contents(self, path: str) -> list:
        """Get list of entries in a directory."""
        entries = []

        if path == "/":
            entries = list(self._tape_names)
        else:
            path_prefix = path if path.endswith("/") else path + "/"
            seen = set()

            for cached_path in self._path_cache:
                if cached_path.startswith(path_prefix):
                    relative = cached_path[len(path_prefix):]
                    if "/" in relative:
                        child = relative.split("/")[0]
                    else:
                        child = relative

                    if child and child not in seen:
                        seen.add(child)
                        entries.append(child)

        return sorted(entries)

    # FUSE Operations - same implementation as CatalogFS

    def getattr(self, path, fh=None):
        """Get file attributes."""
        now = time.time()

        if path == "/":
            return {
                'st_mode': stat.S_IFDIR | 0o555,
                'st_nlink': 2 + len(self._tape_names),
                'st_uid': os.getuid(),
                'st_gid': os.getgid(),
                'st_size': 0,
                'st_atime': now,
                'st_mtime': self._mount_time,
                'st_ctime': self._mount_time,
            }

        if path in self._path_cache:
            is_dir, size, mtime, tape_name = self._path_cache[path]

            if is_dir:
                return {
                    'st_mode': stat.S_IFDIR | 0o555,
                    'st_nlink': 2,
                    'st_uid': os.getuid(),
                    'st_gid': os.getgid(),
                    'st_size': 0,
                    'st_atime': now,
                    'st_mtime': mtime,
                    'st_ctime': mtime,
                }
            else:
                return {
                    'st_mode': stat.S_IFREG | 0o444,
                    'st_nlink': 1,
                    'st_uid': os.getuid(),
                    'st_gid': os.getgid(),
                    'st_size': size,
                    'st_atime': now,
                    'st_mtime': mtime,
                    'st_ctime': mtime,
                }

        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        """List directory contents."""
        entries = ['.', '..']
        entries.extend(self._get_directory_contents(path))
        return entries

    def open(self, path, flags):
        """Open a file (read-only)."""
        if path not in self._path_cache:
            raise FuseOSError(errno.ENOENT)

        is_dir, size, mtime, tape_name = self._path_cache[path]
        if is_dir:
            raise FuseOSError(errno.EISDIR)

        if (flags & os.O_WRONLY) or (flags & os.O_RDWR):
            raise FuseOSError(errno.EROFS)

        return 0

    def read(self, path, size, offset, fh):
        """Read file contents - returns informational message."""
        if path not in self._path_cache:
            raise FuseOSError(errno.ENOENT)

        is_dir, file_size, mtime, tape_name = self._path_cache[path]

        message = f"[File is on tape: {tape_name}]\n"
        message += f"Size: {file_size:,} bytes\n"
        message += f"Mount tape {tape_name} to access this file.\n"

        data = message.encode('utf-8')

        if offset >= len(data):
            return b''

        return data[offset:offset + size]

    def statfs(self, path):
        """Get filesystem statistics."""
        summary = self.db.get_summary()

        block_size = 4096
        total_blocks = (summary['total_bytes'] + block_size - 1) // block_size

        return {
            'f_bsize': block_size,
            'f_frsize': block_size,
            'f_blocks': total_blocks,
            'f_bfree': 0,
            'f_bavail': 0,
            'f_files': summary['file_count'],
            'f_ffree': 0,
            'f_favail': 0,
            'f_flag': os.ST_RDONLY,
            'f_namemax': 255,
        }

    # Unsupported write operations
    def write(self, path, data, offset, fh):
        raise FuseOSError(errno.EROFS)

    def create(self, path, mode, fi=None):
        raise FuseOSError(errno.EROFS)

    def mkdir(self, path, mode):
        raise FuseOSError(errno.EROFS)

    def unlink(self, path):
        raise FuseOSError(errno.EROFS)

    def rmdir(self, path):
        raise FuseOSError(errno.EROFS)

    def rename(self, old, new):
        raise FuseOSError(errno.EROFS)

    def chmod(self, path, mode):
        raise FuseOSError(errno.EROFS)

    def chown(self, path, uid, gid):
        raise FuseOSError(errno.EROFS)

    def truncate(self, path, length, fh=None):
        raise FuseOSError(errno.EROFS)

    def utimens(self, path, times=None):
        raise FuseOSError(errno.EROFS)


class CatalogFS(Operations):
    """
    FUSE filesystem that presents tape catalogs with real file sizes.

    Directory structure:
        /mount_point/
        ├── TAPE_NAME_1/
        │   ├── dir1/
        │   │   └── file1.mov (shows real size from index)
        │   └── file2.pdf
        └── TAPE_NAME_2/
            └── ...
    """

    def __init__(self, index_dir: Path, catalog_dir: Optional[Path] = None):
        """
        Initialize CatalogFS.

        Args:
            index_dir: Directory containing LTFS index XML files
            catalog_dir: Optional directory with zero-byte catalog structure
                        (used for tape name discovery if indexes don't have names)
        """
        self.index_dir = Path(index_dir)
        self.catalog_dir = Path(catalog_dir) if catalog_dir else None

        # Cache: tape_name -> LTFSIndex
        self._indexes: Dict[str, LTFSIndex] = {}

        # Cache: path -> (is_dir, size, mtime, tape_name)
        self._path_cache: Dict[str, Tuple[bool, int, float, str]] = {}

        # Timestamp for mount (must be set before _load_indexes)
        self._mount_time = time.time()

        # Load indexes on startup
        self._load_indexes()

    def _load_indexes(self):
        """Load all LTFS index files and build path cache."""
        self._indexes.clear()
        self._path_cache.clear()

        if not self.index_dir.exists():
            return

        # Group indexes by volume UUID, keep latest generation
        volume_indexes: Dict[str, Tuple[int, Path]] = {}

        for index_file in self.index_dir.glob("*.xml"):
            try:
                index = LTFSIndexParser.parse(index_file)
                uuid = index.volume_uuid
                gen = index.generation

                if uuid not in volume_indexes or gen > volume_indexes[uuid][0]:
                    volume_indexes[uuid] = (gen, index_file)
            except Exception:
                continue

        # Load the latest index for each volume
        for uuid, (gen, index_file) in volume_indexes.items():
            try:
                index = LTFSIndexParser.parse(index_file)

                # Try to get tape name from catalog dir or use UUID prefix
                tape_name = self._get_tape_name(uuid, index)

                self._indexes[tape_name] = index
                self._build_path_cache(tape_name, index)
            except Exception:
                continue

    def _get_tape_name(self, uuid: str, index: LTFSIndex) -> str:
        """Get human-readable tape name for a volume UUID."""
        # First try: check catalog directory for matching names
        if self.catalog_dir and self.catalog_dir.exists():
            for tape_dir in self.catalog_dir.iterdir():
                if tape_dir.is_dir():
                    # Could check if this catalog matches the UUID somehow
                    # For now, just use directory names we find
                    pass

        # Use short UUID as name (first 8 chars)
        return uuid[:8].upper()

    def _build_path_cache(self, tape_name: str, index: LTFSIndex):
        """Build path cache for a tape's index."""

        def cache_directory(directory: IndexDirectory, parent_path: str):
            # Build path for this directory
            if parent_path:
                dir_path = f"/{tape_name}{directory.path}"
            else:
                dir_path = f"/{tape_name}"

            # Get mtime
            mtime = self._mount_time
            if directory.modify_time:
                mtime = directory.modify_time.timestamp()

            self._path_cache[dir_path] = (True, 0, mtime, tape_name)

            # Cache files
            for file in directory.files:
                file_path = f"/{tape_name}{file.path}"
                file_mtime = mtime
                if file.modify_time:
                    file_mtime = file.modify_time.timestamp()
                self._path_cache[file_path] = (False, file.size, file_mtime, tape_name)

            # Recurse into subdirectories
            for subdir in directory.subdirs:
                cache_directory(subdir, dir_path)

        # Cache root entry for tape
        self._path_cache[f"/{tape_name}"] = (True, 0, self._mount_time, tape_name)

        # Cache all files and directories
        cache_directory(index.root, "")

    def _get_directory_contents(self, path: str) -> list:
        """Get list of entries in a directory."""
        entries = []

        if path == "/":
            # Root: list all tape names
            entries = list(self._indexes.keys())
        else:
            # Find all direct children of this path
            path_prefix = path if path.endswith("/") else path + "/"
            seen = set()

            for cached_path in self._path_cache:
                if cached_path.startswith(path_prefix):
                    # Get the relative part
                    relative = cached_path[len(path_prefix):]
                    # Get first component (direct child)
                    if "/" in relative:
                        child = relative.split("/")[0]
                    else:
                        child = relative

                    if child and child not in seen:
                        seen.add(child)
                        entries.append(child)

        return sorted(entries)

    # FUSE Operations

    def getattr(self, path, fh=None):
        """Get file attributes."""
        now = time.time()

        if path == "/":
            # Root directory
            return {
                'st_mode': stat.S_IFDIR | 0o555,
                'st_nlink': 2 + len(self._indexes),
                'st_uid': os.getuid(),
                'st_gid': os.getgid(),
                'st_size': 0,
                'st_atime': now,
                'st_mtime': self._mount_time,
                'st_ctime': self._mount_time,
            }

        if path in self._path_cache:
            is_dir, size, mtime, tape_name = self._path_cache[path]

            if is_dir:
                return {
                    'st_mode': stat.S_IFDIR | 0o555,
                    'st_nlink': 2,
                    'st_uid': os.getuid(),
                    'st_gid': os.getgid(),
                    'st_size': 0,
                    'st_atime': now,
                    'st_mtime': mtime,
                    'st_ctime': mtime,
                }
            else:
                return {
                    'st_mode': stat.S_IFREG | 0o444,
                    'st_nlink': 1,
                    'st_uid': os.getuid(),
                    'st_gid': os.getgid(),
                    'st_size': size,
                    'st_atime': now,
                    'st_mtime': mtime,
                    'st_ctime': mtime,
                }

        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        """List directory contents."""
        entries = ['.', '..']
        entries.extend(self._get_directory_contents(path))
        return entries

    def open(self, path, flags):
        """Open a file (read-only)."""
        if path not in self._path_cache:
            raise FuseOSError(errno.ENOENT)

        is_dir, size, mtime, tape_name = self._path_cache[path]
        if is_dir:
            raise FuseOSError(errno.EISDIR)

        # Check for write flags
        if (flags & os.O_WRONLY) or (flags & os.O_RDWR):
            raise FuseOSError(errno.EROFS)

        return 0

    def read(self, path, size, offset, fh):
        """Read file contents - returns informational message."""
        if path not in self._path_cache:
            raise FuseOSError(errno.ENOENT)

        is_dir, file_size, mtime, tape_name = self._path_cache[path]

        # Return a message indicating the file is on tape
        message = f"[File is on tape: {tape_name}]\n"
        message += f"Size: {file_size:,} bytes\n"
        message += f"Mount tape {tape_name} to access this file.\n"

        data = message.encode('utf-8')

        if offset >= len(data):
            return b''

        return data[offset:offset + size]

    def statfs(self, path):
        """Get filesystem statistics."""
        # Calculate total size from all indexes
        total_size = 0
        total_files = 0

        for tape_name, index in self._indexes.items():
            for file in LTFSIndexParser.get_all_files(index):
                total_size += file.size
                total_files += 1

        block_size = 4096
        total_blocks = (total_size + block_size - 1) // block_size

        return {
            'f_bsize': block_size,
            'f_frsize': block_size,
            'f_blocks': total_blocks,
            'f_bfree': 0,
            'f_bavail': 0,
            'f_files': total_files,
            'f_ffree': 0,
            'f_favail': 0,
            'f_flag': os.ST_RDONLY,
            'f_namemax': 255,
        }

    # Unsupported write operations (read-only filesystem)

    def write(self, path, data, offset, fh):
        raise FuseOSError(errno.EROFS)

    def create(self, path, mode, fi=None):
        raise FuseOSError(errno.EROFS)

    def mkdir(self, path, mode):
        raise FuseOSError(errno.EROFS)

    def unlink(self, path):
        raise FuseOSError(errno.EROFS)

    def rmdir(self, path):
        raise FuseOSError(errno.EROFS)

    def rename(self, old, new):
        raise FuseOSError(errno.EROFS)

    def chmod(self, path, mode):
        raise FuseOSError(errno.EROFS)

    def chown(self, path, uid, gid):
        raise FuseOSError(errno.EROFS)

    def truncate(self, path, length, fh=None):
        raise FuseOSError(errno.EROFS)

    def utimens(self, path, times=None):
        raise FuseOSError(errno.EROFS)


def mount_catalogfs(
    mount_point: Path,
    index_dir: Optional[Path] = None,
    catalog_dir: Optional[Path] = None,
    foreground: bool = False,
    allow_other: bool = False,
    use_database: bool = False,
    db_path: Optional[Path] = None,
) -> None:
    """
    Mount the catalog filesystem.

    Args:
        mount_point: Where to mount the filesystem
        index_dir: Directory containing LTFS index XML files (for XML mode)
        catalog_dir: Optional directory with catalog structure
        foreground: Run in foreground (for debugging)
        allow_other: Allow other users to access the mount
        use_database: If True, use SQLite database instead of XML files
        db_path: Path to catalog database (for database mode)
    """
    if not FUSE_AVAILABLE:
        raise ImportError(
            "fusepy is not installed. Install with: pip install fusepy\n"
            "Also ensure FUSE is installed: sudo apt install fuse3"
        )

    mount_point = Path(mount_point)
    mount_point.mkdir(parents=True, exist_ok=True)

    if use_database:
        fs = CatalogFSFromDB(db_path=db_path)
    else:
        if index_dir is None:
            raise ValueError("index_dir is required when not using database mode")
        fs = CatalogFS(index_dir, catalog_dir)

    fuse_options = {
        'foreground': foreground,
        'ro': True,  # Read-only
        'allow_other': allow_other,
        'auto_unmount': True,
    }

    FUSE(fs, str(mount_point), **fuse_options)


def unmount_catalogfs(mount_point: Path) -> bool:
    """
    Unmount the catalog filesystem.

    Args:
        mount_point: Mount point to unmount

    Returns:
        True if successful, False otherwise
    """
    import subprocess

    mount_point = Path(mount_point)

    try:
        # Try fusermount first (Linux)
        result = subprocess.run(
            ['fusermount', '-u', str(mount_point)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True

        # Try umount as fallback
        result = subprocess.run(
            ['umount', str(mount_point)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    except FileNotFoundError:
        return False
