"""
LTFS Tools - Cross-platform LTO tape archive management.

A Python toolkit for managing LTO tape archives using LTFS (Linear Tape File System).
Provides mounting, transfer with verification, MHL generation, and catalog management.
"""

__version__ = "0.1.0"

from .config import Config, get_config, set_config
from .hash import hash_file, verify_hash
from .mhl import MHL, HashEntry, CreatorInfo, TapeInfo
from .mount import mount, unmount, get_tape_info, MountError
from .transfer import transfer, TransferResult, TransferError
from .verify import verify, VerifyResult, VerifyError
from .catalog import (
    create_catalog,
    create_catalog_from_index,
    update_catalog_from_latest_index,
    list_catalog,
    list_tapes,
    search_catalogs,
)
from .catalog_db import (
    CatalogDB,
    SearchResult,
    TapeRecord,
    FileRecord,
    TapeStats,
    get_catalog_db,
    search as db_search,
    find_by_hash,
)
from .utils import normalize_path

__all__ = [
    # Version
    "__version__",
    # Config
    "Config",
    "get_config",
    "set_config",
    # Hash
    "hash_file",
    "verify_hash",
    # MHL
    "MHL",
    "HashEntry",
    "CreatorInfo",
    "TapeInfo",
    # Mount
    "mount",
    "unmount",
    "get_tape_info",
    "MountError",
    # Transfer
    "transfer",
    "TransferResult",
    "TransferError",
    # Verify
    "verify",
    "VerifyResult",
    "VerifyError",
    # Catalog (filesystem)
    "create_catalog",
    "create_catalog_from_index",
    "update_catalog_from_latest_index",
    "list_catalog",
    "list_tapes",
    "search_catalogs",
    # Catalog (database)
    "CatalogDB",
    "SearchResult",
    "TapeRecord",
    "FileRecord",
    "TapeStats",
    "get_catalog_db",
    "db_search",
    "find_by_hash",
    # Utils
    "normalize_path",
]
