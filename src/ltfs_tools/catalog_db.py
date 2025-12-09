"""
SQLite-based catalog database for fast cross-tape search.

Provides faster search capabilities than the zero-byte file catalogs,
with support for hash-based duplicate detection and rich queries.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config import Config, get_config
from .utils import normalize_path


# Schema version for migrations
SCHEMA_VERSION = 1


@dataclass
class TapeRecord:
    """A tape record in the database."""
    name: str
    volume_uuid: Optional[str] = None
    barcode: Optional[str] = None
    created_at: Optional[datetime] = None
    total_bytes: int = 0
    file_count: int = 0


@dataclass
class FileRecord:
    """A file record in the database."""
    id: int
    tape_name: str
    path: str
    size: int
    mtime: Optional[datetime] = None
    xxhash: Optional[str] = None
    archived_at: Optional[datetime] = None


@dataclass
class SearchResult:
    """A search result with tape and file info."""
    tape_name: str
    path: str
    size: int
    mtime: Optional[datetime] = None
    xxhash: Optional[str] = None


@dataclass
class TapeStats:
    """Statistics for a tape."""
    name: str
    file_count: int
    total_bytes: int
    oldest_file: Optional[datetime] = None
    newest_file: Optional[datetime] = None


class CatalogDB:
    """SQLite-based catalog database."""

    def __init__(self, db_path: Optional[Path] = None, config: Optional[Config] = None):
        """
        Initialize the catalog database.

        Args:
            db_path: Path to the SQLite database file.
                    If None, uses config.archive_base / "catalog.db"
            config: Configuration object
        """
        if config is None:
            config = get_config()

        if db_path is None:
            db_path = config.archive_base / "catalog.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database on first access
        self._init_db()

    @contextmanager
    def _connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize the database schema."""
        with self._connection() as conn:
            cursor = conn.cursor()

            # Create schema version table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)

            # Check current version
            cursor.execute("SELECT version FROM schema_version LIMIT 1")
            row = cursor.fetchone()
            current_version = row["version"] if row else 0

            if current_version < SCHEMA_VERSION:
                self._migrate(conn, current_version)

    def _migrate(self, conn: sqlite3.Connection, from_version: int):
        """Run database migrations."""
        cursor = conn.cursor()

        if from_version < 1:
            # Initial schema
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tapes (
                    name TEXT PRIMARY KEY,
                    volume_uuid TEXT,
                    barcode TEXT,
                    created_at TEXT,
                    total_bytes INTEGER DEFAULT 0,
                    file_count INTEGER DEFAULT 0
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tape_name TEXT NOT NULL REFERENCES tapes(name) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime TEXT,
                    xxhash TEXT,
                    archived_at TEXT,
                    UNIQUE(tape_name, path)
                )
            """)

            # Indexes for fast search
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_files_path
                ON files(path)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_files_xxhash
                ON files(xxhash)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_files_tape
                ON files(tape_name)
            """)

            # Full-text search for filename patterns
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
                USING fts5(path, content='files', content_rowid='id')
            """)

            # Triggers to keep FTS in sync
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                    INSERT INTO files_fts(rowid, path) VALUES (new.id, new.path);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                    INSERT INTO files_fts(files_fts, rowid, path) VALUES('delete', old.id, old.path);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                    INSERT INTO files_fts(files_fts, rowid, path) VALUES('delete', old.id, old.path);
                    INSERT INTO files_fts(rowid, path) VALUES (new.id, new.path);
                END
            """)

        # Update schema version
        cursor.execute("DELETE FROM schema_version")
        cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    def add_tape(
        self,
        name: str,
        volume_uuid: Optional[str] = None,
        barcode: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> None:
        """
        Add or update a tape record.

        Args:
            name: Tape name (primary key)
            volume_uuid: LTFS volume UUID
            barcode: Physical barcode
            created_at: When the tape was created/formatted
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            created_str = created_at.isoformat() if created_at else None

            cursor.execute("""
                INSERT INTO tapes (name, volume_uuid, barcode, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    volume_uuid = COALESCE(excluded.volume_uuid, tapes.volume_uuid),
                    barcode = COALESCE(excluded.barcode, tapes.barcode),
                    created_at = COALESCE(excluded.created_at, tapes.created_at)
            """, (name, volume_uuid, barcode, created_str))

    def add_files(
        self,
        tape_name: str,
        files: list[tuple[str, int, Optional[datetime], Optional[str]]],
        archived_at: Optional[datetime] = None,
    ) -> int:
        """
        Add files to a tape's catalog.

        Args:
            tape_name: Name of the tape
            files: List of (path, size, mtime, xxhash) tuples
            archived_at: When the files were archived (default: now)

        Returns:
            Number of files added
        """
        if not files:
            return 0

        if archived_at is None:
            archived_at = datetime.now(timezone.utc)

        archived_str = archived_at.isoformat()

        with self._connection() as conn:
            cursor = conn.cursor()

            # Ensure tape exists
            cursor.execute(
                "INSERT OR IGNORE INTO tapes (name) VALUES (?)",
                (tape_name,)
            )

            # Insert files
            added = 0
            total_bytes = 0

            for path, size, mtime, xxhash in files:
                mtime_str = mtime.isoformat() if mtime else None
                # Normalize path to NFC for cross-platform consistency
                normalized_path = normalize_path(path)

                try:
                    cursor.execute("""
                        INSERT INTO files (tape_name, path, size, mtime, xxhash, archived_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(tape_name, path) DO UPDATE SET
                            size = excluded.size,
                            mtime = excluded.mtime,
                            xxhash = excluded.xxhash,
                            archived_at = excluded.archived_at
                    """, (tape_name, normalized_path, size, mtime_str, xxhash, archived_str))
                    added += 1
                    total_bytes += size
                except sqlite3.Error:
                    continue

            # Update tape stats
            cursor.execute("""
                UPDATE tapes SET
                    file_count = (SELECT COUNT(*) FROM files WHERE tape_name = ?),
                    total_bytes = (SELECT COALESCE(SUM(size), 0) FROM files WHERE tape_name = ?)
                WHERE name = ?
            """, (tape_name, tape_name, tape_name))

            return added

    def search(
        self,
        pattern: str,
        tape_name: Optional[str] = None,
        limit: int = 1000,
    ) -> list[SearchResult]:
        """
        Search for files matching a pattern.

        Args:
            pattern: Search pattern (supports * and ? wildcards, or FTS queries)
            tape_name: Optional tape to limit search to
            limit: Maximum number of results

        Returns:
            List of SearchResult objects
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            # Normalize pattern to NFC for cross-platform consistency
            pattern = normalize_path(pattern)

            # Convert glob pattern to SQL LIKE pattern
            sql_pattern = pattern.replace("*", "%").replace("?", "_")

            # Check if it's a simple filename search or path search
            if "/" not in pattern and "\\" not in pattern:
                # Search just the filename part
                sql_pattern = "%" + sql_pattern

            if tape_name:
                cursor.execute("""
                    SELECT tape_name, path, size, mtime, xxhash
                    FROM files
                    WHERE tape_name = ? AND path LIKE ?
                    ORDER BY path
                    LIMIT ?
                """, (tape_name, sql_pattern, limit))
            else:
                cursor.execute("""
                    SELECT tape_name, path, size, mtime, xxhash
                    FROM files
                    WHERE path LIKE ?
                    ORDER BY tape_name, path
                    LIMIT ?
                """, (sql_pattern, limit))

            results = []
            for row in cursor.fetchall():
                mtime = None
                if row["mtime"]:
                    try:
                        mtime = datetime.fromisoformat(row["mtime"])
                    except ValueError:
                        pass

                results.append(SearchResult(
                    tape_name=row["tape_name"],
                    path=row["path"],
                    size=row["size"],
                    mtime=mtime,
                    xxhash=row["xxhash"],
                ))

            return results

    def search_fts(
        self,
        query: str,
        tape_name: Optional[str] = None,
        limit: int = 1000,
    ) -> list[SearchResult]:
        """
        Full-text search for files.

        Args:
            query: FTS5 query (e.g., "project AND mov")
            tape_name: Optional tape to limit search to
            limit: Maximum number of results

        Returns:
            List of SearchResult objects
        """
        # Normalize query to NFC for cross-platform consistency
        query = normalize_path(query)

        with self._connection() as conn:
            cursor = conn.cursor()

            if tape_name:
                cursor.execute("""
                    SELECT f.tape_name, f.path, f.size, f.mtime, f.xxhash
                    FROM files f
                    JOIN files_fts fts ON f.id = fts.rowid
                    WHERE fts.path MATCH ? AND f.tape_name = ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, tape_name, limit))
            else:
                cursor.execute("""
                    SELECT f.tape_name, f.path, f.size, f.mtime, f.xxhash
                    FROM files f
                    JOIN files_fts fts ON f.id = fts.rowid
                    WHERE fts.path MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))

            results = []
            for row in cursor.fetchall():
                mtime = None
                if row["mtime"]:
                    try:
                        mtime = datetime.fromisoformat(row["mtime"])
                    except ValueError:
                        pass

                results.append(SearchResult(
                    tape_name=row["tape_name"],
                    path=row["path"],
                    size=row["size"],
                    mtime=mtime,
                    xxhash=row["xxhash"],
                ))

            return results

    def find_by_hash(self, xxhash: str) -> list[SearchResult]:
        """
        Find all files with a specific hash.

        Useful for finding duplicates across tapes or checking if
        a local file already exists in the archive.

        Args:
            xxhash: XXHash64 hex string

        Returns:
            List of SearchResult objects
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT tape_name, path, size, mtime, xxhash
                FROM files
                WHERE xxhash = ?
                ORDER BY tape_name, path
            """, (xxhash,))

            results = []
            for row in cursor.fetchall():
                mtime = None
                if row["mtime"]:
                    try:
                        mtime = datetime.fromisoformat(row["mtime"])
                    except ValueError:
                        pass

                results.append(SearchResult(
                    tape_name=row["tape_name"],
                    path=row["path"],
                    size=row["size"],
                    mtime=mtime,
                    xxhash=row["xxhash"],
                ))

            return results

    def find_duplicates(self, min_size: int = 0) -> Iterator[tuple[str, list[SearchResult]]]:
        """
        Find duplicate files across all tapes.

        Args:
            min_size: Minimum file size to consider (default: 0)

        Yields:
            (xxhash, list of SearchResult) tuples for each duplicate set
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            # Find hashes that appear more than once
            cursor.execute("""
                SELECT xxhash, COUNT(*) as count
                FROM files
                WHERE xxhash IS NOT NULL AND size >= ?
                GROUP BY xxhash
                HAVING count > 1
                ORDER BY count DESC
            """, (min_size,))

            for row in cursor.fetchall():
                xxhash = row["xxhash"]
                results = self.find_by_hash(xxhash)
                yield xxhash, results

    def get_tape_stats(self, tape_name: str) -> Optional[TapeStats]:
        """
        Get statistics for a tape.

        Args:
            tape_name: Name of the tape

        Returns:
            TapeStats object or None if tape not found
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    t.name,
                    t.file_count,
                    t.total_bytes,
                    MIN(f.mtime) as oldest,
                    MAX(f.mtime) as newest
                FROM tapes t
                LEFT JOIN files f ON t.name = f.tape_name
                WHERE t.name = ?
                GROUP BY t.name
            """, (tape_name,))

            row = cursor.fetchone()
            if not row:
                return None

            oldest = None
            if row["oldest"]:
                try:
                    oldest = datetime.fromisoformat(row["oldest"])
                except ValueError:
                    pass

            newest = None
            if row["newest"]:
                try:
                    newest = datetime.fromisoformat(row["newest"])
                except ValueError:
                    pass

            return TapeStats(
                name=row["name"],
                file_count=row["file_count"] or 0,
                total_bytes=row["total_bytes"] or 0,
                oldest_file=oldest,
                newest_file=newest,
            )

    def list_tapes(self) -> list[TapeRecord]:
        """
        List all tapes in the database.

        Returns:
            List of TapeRecord objects
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT name, volume_uuid, barcode, created_at, total_bytes, file_count
                FROM tapes
                ORDER BY name
            """)

            results = []
            for row in cursor.fetchall():
                created_at = None
                if row["created_at"]:
                    try:
                        created_at = datetime.fromisoformat(row["created_at"])
                    except ValueError:
                        pass

                results.append(TapeRecord(
                    name=row["name"],
                    volume_uuid=row["volume_uuid"],
                    barcode=row["barcode"],
                    created_at=created_at,
                    total_bytes=row["total_bytes"] or 0,
                    file_count=row["file_count"] or 0,
                ))

            return results

    def delete_tape(self, tape_name: str) -> bool:
        """
        Delete a tape and all its files from the database.

        Args:
            tape_name: Name of the tape to delete

        Returns:
            True if tape was deleted, False if not found
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            # Delete files first (CASCADE should handle this, but be explicit)
            cursor.execute("DELETE FROM files WHERE tape_name = ?", (tape_name,))
            cursor.execute("DELETE FROM tapes WHERE name = ?", (tape_name,))

            return cursor.rowcount > 0

    def get_summary(self) -> dict:
        """
        Get summary statistics for the entire database.

        Returns:
            Dictionary with tape_count, file_count, total_bytes
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(DISTINCT name) as tape_count,
                    SUM(file_count) as file_count,
                    SUM(total_bytes) as total_bytes
                FROM tapes
            """)

            row = cursor.fetchone()

            return {
                "tape_count": row["tape_count"] or 0,
                "file_count": row["file_count"] or 0,
                "total_bytes": row["total_bytes"] or 0,
            }

    def import_from_mhl(self, mhl_path: Path, tape_name: Optional[str] = None) -> int:
        """
        Import files from an MHL file into the database.

        Args:
            mhl_path: Path to the MHL file
            tape_name: Tape name (default: from MHL tapeinfo or filename)

        Returns:
            Number of files imported
        """
        from .mhl import MHL

        mhl = MHL.load(mhl_path)

        # Determine tape name
        if tape_name is None:
            if mhl.tape_info and mhl.tape_info.name:
                tape_name = mhl.tape_info.name
            else:
                # Extract from filename (e.g., "TAPE01_source_20250101.mhl")
                tape_name = mhl_path.stem.split("_")[0]

        # Add tape record
        self.add_tape(
            name=tape_name,
            barcode=mhl.tape_info.serial if mhl.tape_info else None,
        )

        # Prepare file records
        files = []
        for entry in mhl.hashes:
            files.append((
                entry.file,
                entry.size,
                entry.last_modification_date,
                entry.xxhash64be,
            ))

        return self.add_files(
            tape_name=tape_name,
            files=files,
            archived_at=mhl.creator_info.finish_date,
        )


# Module-level convenience functions

_default_db: Optional[CatalogDB] = None


def get_catalog_db(config: Optional[Config] = None) -> CatalogDB:
    """Get the default catalog database instance."""
    global _default_db

    if _default_db is None:
        _default_db = CatalogDB(config=config)

    return _default_db


def search(
    pattern: str,
    tape_name: Optional[str] = None,
    config: Optional[Config] = None,
) -> list[SearchResult]:
    """Search for files matching a pattern."""
    db = get_catalog_db(config)
    return db.search(pattern, tape_name)


def find_by_hash(xxhash: str, config: Optional[Config] = None) -> list[SearchResult]:
    """Find all files with a specific hash."""
    db = get_catalog_db(config)
    return db.find_by_hash(xxhash)
