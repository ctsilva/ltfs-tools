# CLAUDE.md - LTFS Tools (Python)

## Project Overview

Cross-platform Python toolkit for managing LTO tape archives using LTFS (Linear Tape File System). This project replicates core functionality of commercial tools like Canister, YoYotta, and Hedge.

## Background Context

This project was developed by reverse-engineering how Canister (a commercial macOS LTFS GUI) works:

1. **Mounting**: Canister calls the LTFS binary directly with options like `sync_type=time@5`
2. **Transfers**: Uses standard `rsync` for file copying
3. **Verification**: XXHash64 (big-endian) for fast integrity checking
4. **Outputs**: MHL files (industry-standard XML), transfer logs, and zero-byte catalogs

## Project Structure

```
ltfs-tools/
├── pyproject.toml              # Package config, dependencies, entry points
├── README.md                   # User documentation
├── CLAUDE.md                   # This file - AI assistant context
├── benchmarks/                 # Performance benchmarking scripts
├── src/
│   └── ltfs_tools/
│       ├── __init__.py         # Public API exports
│       ├── cli.py              # Click-based CLI
│       ├── config.py           # Platform detection, settings
│       ├── mount.py            # Mount/unmount operations
│       ├── transfer.py         # Transfer with verification
│       ├── verify.py           # MHL verification
│       ├── mhl.py              # MHL read/write
│       ├── catalog.py          # Catalog management (zero-byte files)
│       ├── catalog_db.py       # SQLite catalog database
│       ├── catalogfs.py        # FUSE virtual filesystem
│       ├── ltfs_index.py       # LTFS index XML parser
│       └── hash.py             # XXHash wrapper
└── tests/
    ├── __init__.py
    └── test_ltfs_tools.py      # pytest tests
```

## Key Design Decisions

### Why Python over Bash?

- Cross-platform without platform conditionals scattered everywhere
- Proper data structures (dicts vs Bash 4+ associative arrays)
- Clean XML handling with `xml.etree.ElementTree`
- Easy testing with pytest
- Rich progress bars and CLI with `click` and `rich`
- Can be packaged and installed with pip

### Dependencies

- `xxhash` - Native Python bindings for XXHash64
- `click` - CLI framework
- `rich` - Beautiful terminal output, progress bars
- `xattr` - Extended attributes for reading LTFS metadata (Linux/macOS only)

### Platform Detection

`config.py` handles platform differences:

| Aspect | macOS | Linux |
|--------|-------|-------|
| LTFS binary | `/Library/Frameworks/LTFS.framework/...` | `/usr/local/bin/ltfs` |
| mkltfs binary | - | `/usr/local/bin/mkltfs` |
| Device (mount) | `0`, `1` (device index) | Auto-detected via `lsscsi -g` |
| Device (format) | - | `/dev/st0` (tape/streaming) |
| Mount point | `/Volumes/LTFS` | `/media/tape` |
| xattr prefix | `ltfs.*` | `user.ltfs.*` |

**Linux Device Auto-Detection**: On Linux, the tape device is automatically detected using `lsscsi -g`, which shows SCSI devices with their generic device paths. This avoids hardcoding device paths like `/dev/sg1` which can change between reboots or when devices are added/removed.

**Important Linux Note**: Linux LTFS uses different device paths for different operations:
- **For mounting** (`ltfs`): Use `/dev/sgN` (SCSI generic device) - auto-detected
- **For formatting** (`mkltfs`): Use `/dev/stN` (tape/streaming device) - e.g., `/dev/st0`

These refer to the same physical tape drive, just different kernel interfaces.

## LTFS Index Files

LTFS maintains an XML index file that contains complete filesystem metadata. This is the key insight that enabled us to replicate Canister's catalog functionality.

### What's in an LTFS Index?

- **Volume metadata**: UUID, generation number, format version
- **Complete directory tree**: Full filesystem structure
- **File metadata**: Paths, sizes, timestamps (modify, create, change, access)
- **Physical locations**: Partition (a/b), block numbers, byte offsets for each file
- **Software info**: Creator, vendor, product, version

### Automatic Index Backup

When mounting a tape, ltfs-tools automatically captures index backups using:
```bash
ltfs -o capture_index=/path/to/indexes
```

Indexes are saved to `~/ltfs-archives/indexes/` as XML files named:
```
{volume-uuid}-{generation}-{partition}.xml
```

Example: `001c2668-aa66-475e-a211-bfcfb7b64712-226-b.xml`

### Index Structure (Simplified)

```xml
<ltfsindex version="2.4.0" xmlns="http://www.ibm.com/xmlns/ltfs">
    <volumeuuid>001c2668-aa66-475e-a211-bfcfb7b64712</volumeuuid>
    <generationnumber>226</generationnumber>
    <directory>
        <name>/</name>
        <contents>
            <file>
                <name>document.pdf</name>
                <length>1234567</length>
                <modifytime>2025-12-06T15:30:00Z</modifytime>
                <extentinfo>
                    <partition>b</partition>
                    <startblock>1234</startblock>
                    <byteoffset>0</byteoffset>
                    <bytecount>1234567</bytecount>
                </extentinfo>
            </file>
        </contents>
    </directory>
</ltfsindex>
```

## Catalog System (Canister-Style)

The catalog system enables **offline browsing** of tape contents without mounting the tape.

### How It Works

1. LTFS index is automatically captured during mount
2. Parse index XML to extract complete filesystem tree
3. Create zero-byte placeholder files mirroring exact structure
4. Preserve timestamps and directory hierarchy
5. Search using standard filesystem tools or Python API

### Catalog Directory Structure

```
~/ltfs-archives/catalogs/
└── BACKUP01/
    ├── Documents/
    │   ├── report.pdf      (0 bytes, preserves mtime)
    │   └── presentation.pptx (0 bytes, preserves mtime)
    └── Videos/
        └── project.mov     (0 bytes, preserves mtime)
```

### Key Advantage

Search for files across all tapes **without mounting any tapes**:
```python
results = search_catalogs("*.mov")
# Returns: [("BACKUP01", "Videos/project.mov"), ("BACKUP02", "Footage/scene1.mov")]
```

### CatalogFS - FUSE Virtual Filesystem

CatalogFS mounts all tape catalogs as a virtual filesystem using FUSE. Unlike the zero-byte catalog directories, CatalogFS shows **real file sizes** from LTFS index files.

**Implementation**: `src/ltfs_tools/catalogfs.py`

**Key classes:**
- `CatalogFS(Operations)` - FUSE filesystem implementation
- Implements: `getattr`, `readdir`, `open`, `read`, `statfs`
- All write operations return `EROFS` (read-only filesystem)

**How it works:**
1. On mount, loads all LTFS index XML files from `~/ltfs-archives/indexes/`
2. Groups indexes by volume UUID, keeps only latest generation
3. Builds path cache: `{path: (is_dir, size, mtime, tape_name)}`
4. FUSE calls query the path cache for file attributes
5. Reading a file returns an info message about which tape contains it

**Directory structure:**
```
/mount_point/
├── TAPE_NAME_1/           (from volume UUID prefix)
│   ├── dir1/
│   │   └── file1.mov      (shows real size: 1.2 GB)
│   └── file2.pdf          (shows real size: 5.4 MB)
└── TAPE_NAME_2/
    └── ...
```

**CLI commands:**
```bash
ltfs-tool catalog mount /mnt/catalogs [-f] [--allow-other]
ltfs-tool catalog mount /mnt/catalogs --db    # Use SQLite (faster)
ltfs-tool catalog unmount /mnt/catalogs
```

**Dependencies:**
- `fusepy>=3.0.0` (optional, install with `pip install ltfs-tools[fuse]`)
- FUSE kernel module (`sudo apt install fuse3` on Linux, `brew install macfuse` on macOS)

## SQLite Catalog Database

The catalog database provides **fast cross-tape search** as an alternative to the zero-byte file catalogs. It's automatically populated during transfers and can be queried without mounting any tapes.

**Implementation**: `src/ltfs_tools/catalog_db.py`

### Database Schema

```sql
CREATE TABLE tapes (
    name TEXT PRIMARY KEY,
    volume_uuid TEXT,
    barcode TEXT,
    created_at TEXT,
    total_bytes INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0
);

CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tape_name TEXT NOT NULL REFERENCES tapes(name),
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime TEXT,
    xxhash TEXT,
    archived_at TEXT,
    UNIQUE(tape_name, path)
);

-- Indexes for fast search
CREATE INDEX idx_files_path ON files(path);
CREATE INDEX idx_files_xxhash ON files(xxhash);
CREATE INDEX idx_files_tape ON files(tape_name);

-- Full-text search
CREATE VIRTUAL TABLE files_fts USING fts5(path, content='files', content_rowid='id');
```

### Key Features

- **Fast wildcard search**: `ltfs-tool catalog db-search "*.mov"`
- **Full-text search**: `ltfs-tool catalog db-search "project AND 2024" --fts`
- **Hash lookup**: Find files by XXHash64 to check if already archived
- **Duplicate detection**: Find files that exist on multiple tapes
- **Summary statistics**: File counts and sizes per tape

### CLI Commands

```bash
# Initialize database (optionally import existing MHLs)
ltfs-tool catalog db-init
ltfs-tool catalog db-init --import-mhls

# Search for files
ltfs-tool catalog db-search "*.mov"                    # Wildcard search
ltfs-tool catalog db-search "*.mov" --summary          # Show totals by tape
ltfs-tool catalog db-search "*.mov" --tape BACKUP01    # Limit to one tape
ltfs-tool catalog db-search "project AND mov" --fts    # Full-text search

# Database statistics
ltfs-tool catalog db-stats                  # Summary of all tapes
ltfs-tool catalog db-stats BACKUP01         # Stats for specific tape

# Find by hash (check if file exists in archive)
ltfs-tool catalog db-find-hash abc123def456

# Find duplicates across tapes
ltfs-tool catalog db-duplicates
ltfs-tool catalog db-duplicates --min-size 10485760   # Only files > 10MB

# Import an MHL file
ltfs-tool catalog db-import archive.mhl
ltfs-tool catalog db-import archive.mhl --tape BACKUP01
```

### Integration with Transfer Workflow

The database is automatically updated during Phase 5 of transfers:

1. Zero-byte catalog files created (legacy/browsable)
2. SQLite database updated with file metadata and hashes

This dual approach maintains backward compatibility while enabling fast search.

### Python API

```python
from ltfs_tools import CatalogDB, get_catalog_db, db_search, find_by_hash

# Get default database instance
db = get_catalog_db()

# Or create with custom path
db = CatalogDB(db_path=Path("/custom/path/catalog.db"))

# Search for files
results = db.search("*.mov")
for r in results:
    print(f"[{r.tape_name}] {r.path} ({r.size} bytes)")

# Full-text search
results = db.search_fts("project AND 2024")

# Find by hash
results = db.find_by_hash("abc123def456")

# Find duplicates
for xxhash, files in db.find_duplicates(min_size=1048576):
    print(f"{xxhash}: {len(files)} copies")

# Get tape statistics
stats = db.get_tape_stats("BACKUP01")
print(f"Files: {stats.file_count}, Size: {stats.total_bytes}")

# Import from MHL
db.import_from_mhl(Path("archive.mhl"), tape_name="BACKUP01")
```

### Database Location

Default: `~/ltfs-archives/catalog.db`

Override with `LTFS_ARCHIVE_BASE` environment variable.

## MHL Format (Media Hash List)

Industry-standard XML format. Key elements:

```xml
<hashlist version="1.1">
    <creatorinfo>...</creatorinfo>
    <tapeinfo><name>TAPE01</name></tapeinfo>
    <hash>
        <file>relative/path.ext</file>
        <size>1234567890</size>
        <lastmodificationdate>2025-01-15T10:30:00Z</lastmodificationdate>
        <xxhash64be>abc123def456789</xxhash64be>
        <hashdate>2025-12-06T15:30:00Z</hashdate>
    </hash>
</hashlist>
```

## Hardware Context (Development Environments)

### macOS Development
- **Drive**: mLogic mTape (Thunderbolt) with IBM LTO-6 (ULTRIUM-HH6)
- **HBA**: ATTO ExpressSAS H1208
- **Firmware**: KAJ9
- **LTFS Version**: 2.4.5.1

### Linux Development (Jupiter server)
- **Drive**: IBM LTO-9 (ULTRIUM-HH9)
- **Device paths**: `/dev/st0` (formatting), `/dev/sg3` (mounting) - auto-detected via `lsscsi -g`
- **OS**: Ubuntu 24.04
- **LTFS**: Custom build in `/usr/local/bin/`
- **Firmware**: P371

### Performance Benchmarks (10 GB transfers)

#### Linux LTO-9 (IBM LTFS)

| File Size | Files | Phase 2 (Write) | Phase 3 (Verify) | Overall |
|-----------|-------|-----------------|------------------|---------|
| 10 MB | 1,024 | **220.6 MB/s** | **289.4 MB/s** | 92.0 MB/s |
| 100 MB | 102 | 203.0 MB/s | 217.5 MB/s | 85.6 MB/s |
| 1 MB | 10,240 | 184.0 MB/s | 211.8 MB/s | 76.6 MB/s |

#### macOS LTO-6 (YoYotta LTFS via mLogic mTape)

| File Size | Files | Phase 2 (Write) | Phase 3 (Verify) | Overall |
|-----------|-------|-----------------|------------------|---------|
| 10 MB | 1,024 | 101.4 MB/s | 80.7 MB/s | 44.3 MB/s |
| 100 MB | 102 | **113.1 MB/s** | **88.3 MB/s** | 49.0 MB/s |
| 1 MB | 10,240 | 112.0 MB/s | 76.2 MB/s | 42.5 MB/s |

**Key findings:**
- **Linux LTO-9**: 10 MB files are optimal, verify ~30% faster than write
- **macOS LTO-6**: 100 MB files are optimal, reaching ~113 MB/s (70% of LTO-6 native 160 MB/s)
- macOS verify speeds are affected by page cache (no `vm.drop_caches` on macOS)
- Peak speeds during transfer reach 146 MB/s (near LTO-6 limit)
- Small file penalty is modest on both platforms (~10-15% slower than optimal)

**Platform differences:**
- Linux uses rsync 3.x with `--info=progress2` for better progress display
- macOS uses system rsync 2.6.9 with `--progress` (older, but functional)
- Cache clearing only available on Linux; macOS verify may read from cache

**Time estimates for large transfers:**
| Phase | Linux LTO-9 | macOS LTO-6 | 1 TB (LTO-9) | 1 TB (LTO-6) |
|-------|-------------|-------------|--------------|--------------|
| Phase 1 (Hash) | 500-600 MB/s | 500-600 MB/s* | ~30 min | ~30 min |
| Phase 2 (Transfer) | 180-220 MB/s | 100-115 MB/s | ~1.5 hr | ~2.5 hr |
| Phase 3 (Verify) | 210-290 MB/s | 75-90 MB/s | ~1 hr | ~3 hr |
| **Total** | | | ~3 hr | ~6 hr |

*Hash speed depends on source disk, not tape

**Rule of thumb**: Total time ≈ 3x raw transfer time (hash + transfer + verify)

## Commands Quick Reference

```bash
# Install in development mode
pip install -e ".[dev]"

# Recovery commands (for failed transfers)
ltfs-tool recover /source/path tape_name   # Re-hash from source (fast)
ltfs-tool finalize dir_on_tape             # Re-hash from tape (slow)

# Run CLI (note: command is 'ltfs-tool' to avoid conflict with system ltfs binary)
ltfs-tool mount TAPENAME
ltfs-tool transfer /source TAPENAME
ltfs-tool info
ltfs-tool unmount
ltfs-tool verify /path/to/file.mhl

# Catalog commands (filesystem-based)
ltfs-tool catalog list
ltfs-tool catalog search "*.mov"
ltfs-tool catalog mount /mnt/catalogs       # FUSE virtual filesystem
ltfs-tool catalog mount /mnt/catalogs --db  # FUSE with SQLite backend
ltfs-tool catalog unmount /mnt/catalogs

# Catalog database commands
ltfs-tool catalog db-init --import-mhls     # Initialize and import MHLs
ltfs-tool catalog db-search "*.mov"         # Fast search
ltfs-tool catalog db-search "*.mov" --summary
ltfs-tool catalog db-stats                  # Database summary
ltfs-tool catalog db-find-hash abc123       # Find by hash
ltfs-tool catalog db-duplicates             # Find duplicates
ltfs-tool catalog db-import file.mhl        # Import MHL

# Run tests
pytest
pytest --cov=ltfs_tools

# Format and lint
black src tests
ruff src tests
mypy src
```

## Transfer Workflow

1. **Pre-flight**: Validate source, check mount, count files
2. **Phase 1 - Hash source**: XXHash64 all files, track excluded files
3. **Cache clear**: Drop page cache before transfer (requires sudo)
4. **Phase 2 - Transfer**: rsync with excludes, live output streaming
5. **Cache clear**: Drop page cache before verification (requires sudo)
6. **Phase 3 - Verify**: Hash destination files, compare with source (reads from tape, not cache)
7. **Phase 4 - Generate MHL**: Write industry-standard XML with all hashes
8. **Phase 5 - Update catalog**: Create zero-byte placeholders + update SQLite database

### Cache Clearing for Accurate Verification (Linux Only)

To ensure Phase 3 reads from tape (not Linux page cache), ltfs-tools drops the cache before verification. This requires passwordless sudo for the sysctl command:

```bash
# Add to /etc/sudoers.d/ltfs-drop-cache
echo "USERNAME ALL=(ALL) NOPASSWD: /usr/sbin/sysctl -w vm.drop_caches=3" | sudo tee /etc/sudoers.d/ltfs-drop-cache
sudo chmod 0440 /etc/sudoers.d/ltfs-drop-cache
```

Without this, verification may read from cache at 4000+ MB/s instead of actual tape speeds (~200-300 MB/s).

**Note:** macOS does not support `vm.drop_caches`, so cache clearing is skipped on macOS. Verification speeds may be inflated if files are still in page cache.

### Best Practices for Production Use

**Use tmux for long-running transfers:**
```bash
tmux new -s tape-transfer
ltfs-tool transfer /large/dataset BACKUP-TAPE
# Ctrl+b, d to detach
# tmux attach -t tape-transfer to reconnect
```

This is critical on remote servers where:
- Network connections may drop
- Transfers can take hours or days
- You need to monitor progress from different sessions
- Process continues even if SSH disconnects

**Recommended workflow:**
1. Mount with volume name: `ltfs-tool mount TAPE-NAME`
2. Check tape info: `ltfs-tool info`
3. Start tmux session: `tmux new -s transfer-job`
4. Run transfer inside tmux
5. Monitor logs: `tail -f ~/ltfs-archives/logs/transfer_*.log`
6. Always unmount properly: `ltfs-tool unmount`

### Recovering from Failed Transfers

If a transfer crashes during Phase 4 (MHL) or Phase 5 (Catalog), the files are already safely on tape. Use recovery commands to generate the missing outputs:

**Option 1: recover (fast)** - Re-hash from original source
```bash
ltfs-tool recover /original/source/path tape_name
```
- Reads from fast SSD/disk (~500-600 MB/s)
- Use when original source is still available
- Applies same exclusion patterns as original transfer
- Generates: MHL file + zero-byte catalog + SQLite database entries

**Option 2: finalize (slow)** - Re-hash from tape
```bash
ltfs-tool finalize directory_on_tape
```
- Reads from tape (~200-300 MB/s)
- Use when original source is no longer available
- Reads whatever is actually on the tape
- Generates: MHL file + zero-byte catalog + SQLite database entries

**Example recovery:**
```bash
# Original failed transfer
ltfs-tool transfer /scratch/csilva/deathstar2 deathstar2
# Crashed during MHL generation...

# Recover using fast source
ltfs-tool recover /scratch/csilva/deathstar2 deathstar2
# Generates: MHL file + catalog + database entries in ~1 hour (vs ~2.5 hours from tape)
```

### File Exclusion Tracking

The transfer process tracks excluded files for transparency:
- Files matching exclusion patterns are logged during Phase 1 (hashing)
- Console shows count: "Excluded 14 files matching exclusion patterns"
- Transfer log includes complete list of excluded files
- Summary distinguishes: files counted, transferred, excluded, verified

Example log output:
```
Files counted: 824        # Total files found in source
Files to transfer: 810    # Non-excluded files
Files excluded: 14        # Files matching .DS_Store, .Trashes, etc.
Files verified: 810       # Successfully verified on tape
Files failed: 0           # Hash mismatches or missing files
```

## Implementation Details

### ltfs_index.py - LTFS Index Parser

The LTFS index parser is implemented with structured dataclasses:

```python
@dataclass
class FileExtent:
    """Physical location of file data on tape."""
    partition: str      # 'a' or 'b'
    start_block: int    # Physical block number
    byte_offset: int    # Offset within block
    byte_count: int     # Number of bytes

@dataclass
class IndexFile:
    """File metadata from LTFS index."""
    name: str
    path: str           # Full path from root
    size: int
    modify_time: Optional[datetime]
    create_time: Optional[datetime]
    readonly: bool
    extents: List[FileExtent]  # Physical tape locations
    uid: Optional[str]          # File UID for deduplication

@dataclass
class IndexDirectory:
    """Directory metadata (recursive)."""
    name: str
    path: str
    files: List[IndexFile]
    subdirs: List['IndexDirectory']

@dataclass
class LTFSIndex:
    """Complete parsed index."""
    version: str
    volume_uuid: str
    generation: int
    update_time: Optional[datetime]
    creator: str
    root: IndexDirectory
```

**Key methods:**
- `LTFSIndexParser.parse(path)` - Parse XML file to structured index
- `LTFSIndexParser.get_all_files(index)` - Flatten to file list
- `LTFSIndexParser.get_all_directories(index)` - Flatten to dir list

**XML Namespace handling:**
```python
NS = {'ltfs': 'http://www.ibm.com/xmlns/ltfs'}
# All XML queries use: elem.findtext('ltfs:name', namespaces=NS)
```

### catalog.py - Catalog Management

**create_catalog_from_index()**: The core Canister replication
1. Parse LTFS index XML
2. Recursively walk directory tree
3. Create zero-byte files with `touch()`
4. Preserve timestamps with `os.utime()`

**update_catalog_from_latest_index()**: Convenience wrapper
1. Search index directory for matching files
2. Sort by modification time
3. Use most recent index
4. Call `create_catalog_from_index()`

**search_catalogs()**: Offline search
- Uses `fnmatch` for wildcard patterns
- Case-insensitive matching
- Returns `(tape_name, relative_path)` tuples

### transfer.py - Transfer with Verification

**Key implementation details:**

**Excluded file tracking:**
```python
excluded_files: list[str] = []

for path in source.rglob("*"):
    if _should_exclude(rel_path, config.excludes):
        excluded_files.append(str(rel_path))
        continue
    # Hash and process file...
```

**Progress tracking with Rich:**
- Byte-based progress bars (not file count) for accurate percentage
- Live filename display: `f"Hashing: {str(rel_path)[:60]}"`
- Transfer speed calculation via `TransferSpeedColumn()`
- Data amount display via `DownloadColumn()`

**rsync live output streaming:**
```python
process = subprocess.Popen(
    rsync_cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

for line in process.stdout:
    console.print(line.rstrip())  # Live to console
    log_file.write(line)          # Archive to log
```

**rsync flags:**
- `--info=progress2` - Overall progress summary (Linux only, rsync 3.1+)
- `--no-i-r` - Disable incremental recursion for cleaner output (Linux only)
- `--progress` - Per-file progress (macOS, uses system rsync 2.6.9)
- `--exclude` - Applied for each exclusion pattern

### mount.py - Extended Attributes

**get_tape_attributes()**: Read LTFS metadata from xattrs

Platform-specific attribute prefixes:
- Linux: `user.ltfs.volumeUUID`, `user.ltfs.generation`, etc.
- macOS: `ltfs.volumeUUID`, `ltfs.generation`, etc.

Common attributes:
- `volumeUUID`, `volumeName` - Volume identification
- `generation` - Index generation number
- `softwareVendor`, `softwareProduct`, `softwareVersion` - Creator info
- `barcode`, `mediaPool` - Physical tape metadata

## Known Limitations / TODO

### High Priority
- [ ] Better rsync progress parsing (currently just runs rsync)
- [ ] Resume interrupted transfers
- [ ] Handle Unicode filenames in XML properly (full escaping)
- [ ] Add `--checksum` rsync option for extra verification

### Medium Priority
- [x] SQLite catalog database for fast search (**DONE** - see `catalog_db.py`)
- [ ] Multiple hash algorithms (MD5, SHA1) for compatibility
- [ ] Tape spanning support
- [ ] Email/Slack notifications
- [ ] Catalog snapshots/history (when re-cataloging after writes)
- [ ] Position-sorted restores (read files in tape physical order)

### Nice to Have
- [ ] TUI with `textual`
- [ ] Web dashboard
- [ ] Barcode scanning integration
- [ ] Direct tape recovery from index physical locations

## Code Style

- Use `rich.console.Console` for output
- Use `pathlib.Path` throughout (no string paths)
- Type hints on all public functions
- Docstrings in Google style
- Errors as custom exception classes (`MountError`, `TransferError`, etc.)

## Testing Strategy

- Unit tests for hash, mhl, catalog modules (no tape needed)
- Integration tests mock subprocess calls for mount/transfer
- Use `tempfile` for file-based tests

## Environment Variables

```bash
LTFS_MOUNT_POINT    # Override default mount point
LTFS_DEVICE         # Override default device
LTFS_ARCHIVE_BASE   # Override output directory (~/.ltfs-archives)
```

## File Exclusions (Default)

```python
[
    ".DS_Store",
    ".Spotlight-*",
    ".fseventsd",
    ".Trashes",
    ".Trash/",
    "Library/Caches/",
    "*.tmp",
    ".TemporaryItems",
    "Thumbs.db",
]
```

## API Examples

```python
# Simple transfer
from pathlib import Path
from ltfs_tools import mount, transfer, unmount

mount(volume_name="BACKUP01")
result = transfer(source=Path("/data"), tape_name="BACKUP01")
print(f"MHL: {result.mhl_path}")
unmount()

# Work with MHL files
from ltfs_tools import MHL

mhl = MHL.load(Path("archive.mhl"))
for entry in mhl:
    print(f"{entry.file}: {entry.xxhash64be}")

# Hash a file
from ltfs_tools import hash_file

h = hash_file(Path("video.mov"))

# Parse LTFS index and create catalog (Canister-style)
from ltfs_tools import create_catalog_from_index, LTFSIndexParser

# Parse an index file
index = LTFSIndexParser.parse(Path("~/ltfs-archives/indexes/001c2668-226-a.xml"))
print(f"Volume UUID: {index.volume_uuid}")
print(f"Generation: {index.generation}")

# Get all files from index
all_files = LTFSIndexParser.get_all_files(index)
for file in all_files:
    print(f"{file.path}: {file.size} bytes")

# Create offline catalog from index
catalog_dir = create_catalog_from_index(
    Path("~/ltfs-archives/indexes/001c2668-226-a.xml"),
    tape_name="BACKUP01"
)

# Update catalog from latest index
from ltfs_tools import update_catalog_from_latest_index
catalog_dir = update_catalog_from_latest_index("BACKUP01")

# Search catalogs without mounting tape (filesystem-based)
from ltfs_tools import search_catalogs
results = search_catalogs("*.mov")
for tape_name, file_path in results:
    print(f"{tape_name}: {file_path}")

# SQLite catalog database (faster for large archives)
from ltfs_tools import CatalogDB, get_catalog_db, find_by_hash

db = get_catalog_db()

# Search with wildcards
results = db.search("*.mov")
for r in results:
    print(f"[{r.tape_name}] {r.path} ({r.size} bytes, hash: {r.xxhash})")

# Full-text search
results = db.search_fts("project AND 2024")

# Check if a file is already archived (by hash)
results = find_by_hash("abc123def456")
if results:
    print(f"File already on tape: {results[0].tape_name}")

# Find duplicates across tapes
for xxhash, files in db.find_duplicates(min_size=1048576):
    print(f"Duplicate: {xxhash} on {len(files)} tapes")

# Get tape statistics
stats = db.get_tape_stats("BACKUP01")
print(f"Files: {stats.file_count}, Total: {stats.total_bytes} bytes")

# Import existing MHL files
from pathlib import Path
db.import_from_mhl(Path("archive.mhl"), tape_name="BACKUP01")
```

## Useful Links

- [LTFS Specification](https://www.lto.org/technology/ltfs/)
- [MHL Specification](https://mediahashlist.org/)
- [XXHash](https://github.com/Cyan4973/xxHash)
- [Click Documentation](https://click.palletsprojects.com/)
- [Rich Documentation](https://rich.readthedocs.io/)
