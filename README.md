# LTFS Tools

Cross-platform Python toolkit for managing LTO tape archives using LTFS (Linear Tape File System). This package provides functionality similar to commercial tools like Canister, but with Linux support. It is not intended to replace industry-tested commercial tools, which are more robust and mature.

---

## ⚠️ Important Disclaimer

**This is experimental software under active development.** While we have implemented verification at every step (source hashing, transfer, read-back verification), this code is new and has not been battle-tested in production environments.

**Before using this for important data:**

1. **Do not rely solely on this tool for critical backups.** Always maintain multiple copies of irreplaceable data using different methods and tools.
2. **Verify independently.** After any transfer, consider spot-checking files manually or using additional verification tools.
3. **Test thoroughly.** Run test transfers with non-critical data before trusting this tool with important archives.
4. **Keep your source data.** Do not delete original files until you have independently confirmed the tape contents are correct and readable.
5. **Understand the risks.** Tape drives, LTFS implementations, and this software can all have bugs. A single point of failure in your backup strategy is dangerous.

The authors provide this software as-is, without warranty. Use at your own risk.

---

## Features

- **Cross-platform**: Works on macOS and Linux
- **XXHash64 verification**: Fast, reliable file integrity checking
- **MHL output**: Industry-standard Media Hash List format
- **Automatic LTFS index backup**: Captures tape indexes for recovery
- **Offline catalog system**: Browse tape contents without mounting (Canister-style)
- **CatalogFS (FUSE)**: Mount catalogs as a virtual filesystem with real file sizes
- **Tape metadata extraction**: Read volume UUID, generation, software info via xattrs
- **Rich CLI**: Beautiful progress bars and formatted output
- **Pythonic API**: Use as a library in your own scripts

## Installation

```bash
# From source
git clone https://github.com/ctsilva/ltfs-tools.git
cd ltfs-tools
pip install -e .

# Or with development dependencies
pip install -e ".[dev]"
```

### Requirements

- Python 3.9+
- LTFS drivers (IBM Spectrum Archive or vendor-provided)
- rsync
- xattr Python module (auto-installed)

## Quick Start

```bash
# Mount a tape (auto-captures indexes)
ltfs-tool mount BACKUP01

# Check tape info (fast - no deep scan)
ltfs-tool info

# Transfer files with verification
ltfs-tool transfer /path/to/archive BACKUP01

# Unmount (critical - writes final index!)
ltfs-tool unmount
```

## CLI Commands

### ltfs-tool mount

Mount an LTFS-formatted tape. Automatically captures index backups to `~/ltfs-archives/indexes/`.

```bash
ltfs-tool mount [VOLUME_NAME] [--mount-point PATH] [--device DEVICE]

# Examples
ltfs-tool mount                          # Mount with defaults
ltfs-tool mount BACKUP01                 # Mount with volume name
ltfs-tool mount BACKUP01 -m /mnt/tape    # Custom mount point
```

**Important**: On Linux, the tape device is auto-detected via `lsscsi -g`. On macOS, uses device index `0`.

### ltfs-tool unmount

Safely unmount a tape. **Always do this before ejecting!** This writes the final LTFS index to tape.

```bash
ltfs-tool unmount [--mount-point PATH]
```

### ltfs-tool info

Display information about a mounted tape. Shows:
- Mount point and device
- Volume UUID and generation number
- Software vendor/product/version
- Format specification version
- Top-level directory contents
- Total size (using fast `du` command)

```bash
ltfs-tool info [--mount-point PATH]

# Example output:
# LTFS Tape Information
#
#  Mount Point       /media/tape
#  Platform          linux
#  Device            /dev/sg1
#  Volume UUID       001c2668-aa66-475e-a211-bfcfb7b64712
#  Software Vendor   IBM
#  Software Product  LTFS LE
#  Software Version  2.5.0.0 (Prelim)
#  Format Spec       2.4.0
#
# Top-level Contents:
#   Directories:
#     Downloads/
#     backups/
#     home-old-vida/
```

### ltfs-tool transfer

Transfer files with XXHash verification, logging, and MHL generation.

```bash
ltfs-tool transfer SOURCE [TAPE_NAME] [--dry-run] [--no-verify] [--mount-point PATH]

# Examples
ltfs-tool transfer ~/Documents BACKUP01
ltfs-tool transfer /data/project PROJECT_ARCHIVE
ltfs-tool transfer ~/Documents BACKUP01 --dry-run  # Preview only
ltfs-tool transfer ~/Documents BACKUP01 -m /Volumes/LTFS_TAPE  # Custom mount point
```

**Transfer Process:**
1. **Phase 1**: Hash all source files (XXHash64), tracking excluded files
2. **Phase 2**: Transfer files with rsync (excluding system/temp files)
3. **Phase 3**: Verify destination files by comparing hashes (reads from tape, not cache)
4. **Phase 4**: Generate MHL file with all hashes and metadata
5. **Phase 5**: Update catalog with zero-byte placeholders

**Phase Performance Output:**

After each transfer, detailed timing is shown:
```
Phase Performance
 Phase 1 (Hash source)   16.68s   614.0 MB/s
 Phase 2 (Transfer)      46.42s   220.6 MB/s
 Phase 3 (Verify)        35.38s   289.4 MB/s
 Phase 4 (MHL)            0.04s            —
 Phase 5 (Catalog)        0.05s            —
 Overall                111.34s    92.0 MB/s
```

**File Exclusions**: By default, system and temporary files are excluded:
- `.DS_Store`, `.Spotlight-*`, `.fseventsd`, `.Trashes` (macOS)
- `Thumbs.db` (Windows)
- `*.tmp`, `.TemporaryItems`
- `Library/Caches/`

Excluded files are listed in the transfer log for transparency.

**Long-Running Transfers:**

For large transfers that may take hours, use `tmux` to prevent disconnection from stopping the job:

```bash
# Start a tmux session
tmux new -s tape-transfer

# Inside tmux, run your transfer
ltfs-tool transfer /large/dataset BACKUP01

# Detach from tmux (transfer continues in background)
# Press: Ctrl+b, then d

# Later, reattach to check progress
tmux attach -t tape-transfer

# List all tmux sessions
tmux ls
```

This ensures your transfer continues even if your SSH connection drops. The transfer log at `~/ltfs-archives/logs/` will contain the complete record.

**Outputs:**
- Transfer log: `~/ltfs-archives/logs/` (includes excluded files list)
- MHL file: `~/ltfs-archives/mhl/`
- LTFS index backups: `~/ltfs-archives/indexes/` (automatic)
- Catalog: `~/ltfs-archives/catalogs/` (created from indexes)

### ltfs-tool recover

Recover MHL and catalog after a failed transfer by re-hashing the **source** files (fast).

Use this when a transfer crashed during Phase 4 (MHL) or Phase 5 (Catalog) but the files are already on tape. This reads from the original source (SSD/disk) which is much faster than reading from tape.

```bash
ltfs-tool recover SOURCE [TAPE_NAME]

# Example - recover using original source
ltfs-tool recover /scratch/csilva/deathstar2 deathstar2
```

**Time estimate**: ~500-600 MB/s (limited by source disk speed)

### ltfs-tool finalize

Generate MHL and catalog from **tape** files (slower than recover).

Use this when the original source is no longer available and you need to generate MHL/catalog from the tape itself.

```bash
ltfs-tool finalize SOURCE_NAME [TAPE_NAME]

# Example - finalize from tape
ltfs-tool finalize deathstar2
```

**Time estimate**: ~200-300 MB/s (limited by tape read speed)

### ltfs-tool verify

Verify files against an MHL file.

```bash
ltfs-tool verify MHL_FILE [BASE_PATH]

# Examples
ltfs-tool verify ~/ltfs-archives/mhl/BACKUP01.mhl          # Verify mounted tape
ltfs-tool verify archive.mhl /path/to/restored/files       # Verify restored files
```

### ltfs-tool catalog

Manage tape catalogs. Catalogs are created from LTFS index XML files and allow searching tape contents **without mounting the tape**.

```bash
# List cataloged tapes
ltfs-tool catalog list

# Search across catalogs
ltfs-tool catalog search "*.mov"
ltfs-tool catalog search "project*" --tape BACKUP01
```

#### CatalogFS - Virtual Filesystem (FUSE)

Mount all tape catalogs as a virtual filesystem using FUSE. Files show their **real sizes** from LTFS indexes without consuming disk space. Inspired by Canister's catalog browsing feature.

```bash
# Install FUSE support
pip install ltfs-tools[fuse]
# Also ensure FUSE is installed on your system:
# Linux: sudo apt install fuse3
# macOS: brew install macfuse

# Mount catalogs
ltfs-tool catalog mount /mnt/catalogs

# Browse with standard tools - files show real sizes!
ls -la /mnt/catalogs/
ls -lh /mnt/catalogs/BACKUP01/Videos/
du -sh /mnt/catalogs/BACKUP01/*

# Reading a file shows which tape it's on
cat /mnt/catalogs/BACKUP01/Videos/project.mov
# Output: [File is on tape: BACKUP01]
#         Size: 1,234,567,890 bytes
#         Mount tape BACKUP01 to access this file.

# Run in foreground for debugging
ltfs-tool catalog mount /mnt/catalogs --foreground

# Unmount
ltfs-tool catalog unmount /mnt/catalogs
```

**Features:**
- Shows actual file sizes from LTFS index (not zero-byte placeholders)
- Preserves timestamps from original files
- Read-only filesystem (protects against accidental writes)
- Browse multiple tapes in one mount point
- Works with `ls`, `find`, `du`, and other standard tools

## Python API

### Basic Operations

```python
from pathlib import Path
from ltfs_tools import mount, transfer, verify, unmount

# Mount tape (auto-captures indexes)
mount(volume_name="BACKUP01")

# Transfer files
result = transfer(
    source=Path("/path/to/archive"),
    tape_name="BACKUP01",
)

print(f"Transferred {result.files_verified} files")
print(f"MHL file: {result.mhl_path}")

# Unmount
unmount()
```

### Working with LTFS Indexes & Catalogs

The killer feature - browse tape contents without mounting!

```python
from pathlib import Path
from ltfs_tools import (
    create_catalog_from_index,
    update_catalog_from_latest_index,
    search_catalogs,
    list_tapes
)

# Option 1: Create catalog from specific index file
index_file = Path("~/ltfs-archives/indexes/001c2668-226-b.xml")
catalog_dir = create_catalog_from_index(index_file, tape_name="BACKUP01")

# Option 2: Auto-update from latest index (recommended)
catalog_dir = update_catalog_from_latest_index("BACKUP01")

# List all cataloged tapes
tapes = list_tapes()
print(f"Available tapes: {tapes}")

# Search without mounting the tape!
results = search_catalogs("*.mov")
for tape_name, file_path in results:
    print(f"{tape_name}: {file_path}")
```

### Parsing LTFS Index Files

```python
from pathlib import Path
from ltfs_tools.ltfs_index import LTFSIndexParser

# Parse an LTFS index XML file
index = LTFSIndexParser.parse(Path("tape-index.xml"))

print(f"Volume UUID: {index.volume_uuid}")
print(f"Generation: {index.generation}")
print(f"Format version: {index.version}")

# Get all files
all_files = LTFSIndexParser.get_all_files(index)
for file in all_files:
    print(f"{file.path}: {file.size} bytes")
    print(f"  Physical location: partition {file.extents[0].partition}, "
          f"block {file.extents[0].start_block}")

# Get all directories
all_dirs = LTFSIndexParser.get_all_directories(index)
```

### Working with MHL files

```python
from ltfs_tools import MHL, HashEntry
from pathlib import Path

# Create an MHL
mhl = MHL()
mhl.add_hash(HashEntry(
    file="document.pdf",
    size=1234567,
    xxhash64be="abc123def456789",
))
mhl.save(Path("archive.mhl"))

# Load and iterate
mhl = MHL.load(Path("archive.mhl"))
for entry in mhl:
    print(f"{entry.file}: {entry.xxhash64be}")
```

### Hashing files

```python
from ltfs_tools import hash_file, verify_hash
from pathlib import Path

# Hash a file (XXHash64 big-endian)
file_hash = hash_file(Path("large_video.mov"))

# Verify against known hash
is_valid = verify_hash(Path("large_video.mov"), "expected_hash")
```

### Reading Tape Metadata

```python
from ltfs_tools import get_tape_info
from pathlib import Path

# Get info about mounted tape
info = get_tape_info(Path("/media/tape"))

if info["mounted"]:
    print(f"Volume UUID: {info.get('volumeUUID')}")
    print(f"Generation: {info.get('generation')}")
    print(f"Software: {info.get('softwareProduct')} {info.get('softwareVersion')}")
    print(f"Top-level dirs: {info['top_level_dirs']}")
```

## Configuration

### Environment Variables

```bash
export LTFS_MOUNT_POINT="/media/tape"
export LTFS_DEVICE="/dev/sg1"
export LTFS_ARCHIVE_BASE="/data/tape-archives"
```

### Programmatic Configuration

```python
from ltfs_tools import Config, set_config
from pathlib import Path

config = Config(
    mount_point=Path("/custom/mount"),
    device="/dev/sg1",
    archive_base=Path("/data/archives"),
    excludes=[".DS_Store", "*.tmp", "node_modules/"],
)
set_config(config)
```

### Platform-Specific Defaults

#### Linux
- LTFS binary: `/usr/local/bin/ltfs`
- Default device: `/dev/sg1` (SCSI generic for mounting)
- Format device: `/dev/st0` (tape device for `mkltfs`)
- Default mount: `/media/tape`

#### macOS
- LTFS binary: `/Library/Frameworks/LTFS.framework/.../ltfs`
- Default device: `0` (device index)
- Default mount: `/Volumes/LTFS`

## Output Files

LTFS Tools creates an organized archive structure:

```
~/ltfs-archives/
├── indexes/          # LTFS index backups (XML)
│   ├── 001c2668-aa66-475e-a211-bfcfb7b64712-226-a.xml
│   └── 001c2668-aa66-475e-a211-bfcfb7b64712-226-b.xml
├── catalogs/         # Zero-byte placeholders (Canister-style)
│   └── BACKUP01/
│       ├── Documents/
│       │   ├── file1.pdf    (0 bytes, preserves mtime)
│       │   └── file2.docx   (0 bytes, preserves mtime)
│       └── Videos/
├── mhl/              # Media Hash Lists (XML)
│   └── BACKUP01-20251206.mhl
└── logs/             # Transfer logs
    └── BACKUP01-20251206.log
```

### LTFS Index Files

LTFS indexes are XML files containing complete filesystem metadata:
- Volume UUID, generation number, format version
- Complete directory tree structure
- File metadata (paths, sizes, timestamps)
- **Physical tape locations** (partition, block numbers, byte offsets)
- Used for mounting and recovery

Automatically captured during mount with `-o capture_index`.

### Transfer Logs

Transfer logs provide detailed records of each transfer operation:

```
LTFS Transfer Log
================
Source: /path/to/source
Destination: /media/tape/destination
Tape: BACKUP01
Started: 2025-12-06T23:17:07Z
Files counted: 824
Files to transfer: 810
Files excluded: 14

--- Excluded files ---
  .DS_Store
  .fseventsd/...
  .Trashes/...
  [additional excluded files]

--- rsync output ---
[rsync transfer progress]

--- Summary ---
Finished: 2025-12-06T23:23:16Z
Duration: 369.5s
Files counted: 824
Files transferred: 810
Files excluded: 14
Files verified: 810
Files failed: 0
```

### Catalog System (Canister-Style)

Catalogs enable **offline tape browsing** without mounting:
- Zero-byte placeholder files mirroring tape structure
- Preserves timestamps and directory hierarchy
- Searchable with standard filesystem tools
- Created from LTFS index XML files

### MHL (Media Hash List)

Industry-standard XML format for archival verification:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<hashlist version="1.1">
    <creatorinfo>
        <name>User Name</name>
        <tool>ltfs-tools 0.1.0</tool>
    </creatorinfo>
    <tapeinfo>
        <name>BACKUP01</name>
    </tapeinfo>
    <hash>
        <file>document.pdf</file>
        <size>1234567</size>
        <xxhash64be>abc123def456789</xxhash64be>
        <hashdate>2025-12-06T15:30:00Z</hashdate>
    </hash>
</hashlist>
```

## Performance Expectations

Based on benchmarks with 10 GB transfers:

### Linux LTO-9 (IBM LTFS)

| File Size | Write Speed | Verify Speed | Overall |
|-----------|-------------|--------------|---------|
| 10 MB | 220 MB/s | 289 MB/s | 92 MB/s |
| 100 MB | 203 MB/s | 217 MB/s | 86 MB/s |
| 1 MB | 184 MB/s | 212 MB/s | 77 MB/s |

### macOS LTO-6 (YoYotta LTFS via mLogic mTape)

| File Size | Write Speed | Verify Speed | Overall |
|-----------|-------------|--------------|---------|
| 10 MB | 101 MB/s | 81 MB/s | 44 MB/s |
| 100 MB | 113 MB/s | 88 MB/s | 49 MB/s |
| 1 MB | 112 MB/s | 76 MB/s | 43 MB/s |

**Time estimates for large transfers:**

| Data Size | LTO-9 (Linux) | LTO-6 (macOS) |
|-----------|---------------|---------------|
| 1 TB | ~3 hr | ~6 hr |
| 5 TB | ~15 hr | ~30 hr |
| 10 TB | ~30 hr | ~60 hr |

**Rule of thumb**: Total time ≈ 3× raw transfer time (includes hashing and verification).

## How It Compares to Commercial Tools

| Feature | ltfs-tools | Canister | YoYotta | Hedge |
|---------|------------|----------|---------|-------|
| LTFS Mount/Unmount | ✅ | ✅ | ✅ | ✅ |
| Index Backup | ✅ Auto | ✅ | ✅ | ✅ |
| Offline Catalogs | ✅ | ✅ | ✅ | ✅ |
| MHL Generation | ✅ | ✅ | ✅ | ✅ |
| XXHash64 | ✅ | ✅ | ❌ | ❌ |
| Python API | ✅ | ❌ | ❌ | ❌ |
| Open Source | ✅ | ❌ | ❌ | ❌ |
| Cross-platform | ✅ | macOS only | ✅ | macOS only |
| Price | Free | $299 | Enterprise | $295 |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=ltfs_tools

# Format code
black src tests

# Lint
ruff src tests

# Type check
mypy src
```

## Best Practices

### Long-Running Operations with tmux

For operations that may take hours (large transfers, tape formatting), use `tmux` or `screen` to ensure the process continues even if your SSH session disconnects:

```bash
# Start a named tmux session
tmux new -s backup-job

# Run your long operation inside tmux
ltfs-tool transfer /large/dataset BACKUP-TAPE

# Detach from tmux (keeps process running)
# Press: Ctrl+b, then d

# Reconnect later from any terminal
tmux attach -t backup-job

# List all active sessions
tmux ls

# Kill a session when done
tmux kill-session -t backup-job
```

**Useful tmux commands:**
- `Ctrl+b, d` - Detach from session
- `Ctrl+b, c` - Create new window
- `Ctrl+b, n` - Next window
- `Ctrl+b, p` - Previous window
- `Ctrl+b, [` - Scroll mode (use arrow keys, `q` to exit)

### Transfer Workflow Best Practices

1. **Always mount with a volume name**: `ltfs-tool mount TAPE-NAME`
2. **Check tape info before transfer**: `ltfs-tool info`
3. **Use tmux for large transfers**: Prevents interruption from network issues
4. **Monitor logs**: `tail -f ~/ltfs-archives/logs/transfer_*.log`
5. **Always unmount properly**: `ltfs-tool unmount` writes final index to tape
6. **Verify catalog after transfer**: `ltfs-tool catalog list`

### Tape Care

- **Never power off** without unmounting - this will corrupt the LTFS index
- **Eject only after unmount** - ensures final index is written
- **Label tapes clearly** with both physical labels and volume names
- **Store transfer logs** - they contain excluded files lists and verification results

## Troubleshooting

### Linux: Permission denied when accessing tape

Use `sudo` or add your user to the `tape` group:
```bash
sudo usermod -a -G tape $USER
# Log out and back in for group change to take effect
```

### Linux: Setup for accurate verification

To ensure Phase 3 reads from tape (not Linux page cache), ltfs-tools needs permission to drop the cache. Add this sudoers rule:

```bash
echo "$USER ALL=(ALL) NOPASSWD: /usr/sbin/sysctl -w vm.drop_caches=3" | sudo tee /etc/sudoers.d/ltfs-drop-cache
sudo chmod 0440 /etc/sudoers.d/ltfs-drop-cache
```

Without this, verification may read from memory cache instead of tape, showing unrealistic speeds (4000+ MB/s instead of ~200-300 MB/s).

### macOS: Mount point naming

On macOS, LTFS may create mount points with the tape barcode in the name (e.g., `/Volumes/LTFS_10WT050503_SILV03`). Use the `--mount-point` option to specify the correct path:

```bash
ltfs-tool info --mount-point /Volumes/LTFS_10WT050503_SILV03
ltfs-tool transfer ~/data BACKUP01 --mount-point /Volumes/LTFS_10WT050503_SILV03
```

### macOS: Verification speeds

macOS does not support dropping the page cache (`vm.drop_caches`), so verification speeds may be inflated if recently transferred files are still cached in memory. For accurate verification timing, wait a few minutes or transfer datasets larger than available RAM.

### Index files not being captured

Check that the index directory exists and is writable:
```bash
ls -la ~/ltfs-archives/indexes/
```

Indexes are only written when the LTFS index is updated (every 5 minutes by default with `sync_type=time@5`, or during unmount).

### Catalog search returns no results

Make sure catalogs have been created from indexes:
```python
from ltfs_tools import update_catalog_from_latest_index
update_catalog_from_latest_index("TAPE_NAME")
```

## License

MIT

## Acknowledgments

This project started by loosely reverse-engineering how commercial tools like Canister work with LTFS on macOS. The core insight is that LTFS indexes contain all metadata needed to create offline catalogs. Significant additional engineering was required to support Linux, including automatic device detection, platform-specific mount handling, and page cache management for accurate verification.
