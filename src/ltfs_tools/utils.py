"""
Utility functions for ltfs-tools.
"""

import unicodedata
from pathlib import Path
from typing import Union


def normalize_path(path: Union[str, Path]) -> str:
    """
    Normalize a path string to NFC (Composed) Unicode form.

    This ensures consistent path representation across platforms:
    - macOS HFS+/APFS uses NFD (decomposed): é = e + ́ (two code points)
    - Linux ext4/XFS uses NFC (composed): é = é (one code point)

    By normalizing to NFC, we ensure:
    - MHL files have consistent paths regardless of source platform
    - SQLite catalog searches work cross-platform
    - Verification works when backup and restore are on different platforms

    Args:
        path: A path string or Path object

    Returns:
        NFC-normalized path string
    """
    path_str = str(path)
    return unicodedata.normalize("NFC", path_str)


def normalize_path_for_storage(path: Union[str, Path]) -> str:
    """
    Normalize a relative path for storage in MHL/catalog.

    Same as normalize_path but explicitly for paths that will be
    stored in MHL files or the catalog database.

    Args:
        path: A relative path string or Path object

    Returns:
        NFC-normalized path string
    """
    return normalize_path(path)
