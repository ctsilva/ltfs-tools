"""
Hashing utilities using XXHash64.
"""

from pathlib import Path
from typing import BinaryIO, Callable, Optional

import xxhash

# Default chunk size for reading files (1MB)
DEFAULT_CHUNK_SIZE = 1024 * 1024


def hash_file(
    filepath: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """
    Calculate XXHash64 of a file.

    Args:
        filepath: Path to file to hash
        chunk_size: Size of chunks to read
        progress_callback: Optional callback(bytes_read, total_bytes) for progress

    Returns:
        Hex string of the hash (16 characters)
    """
    hasher = xxhash.xxh64()
    file_size = filepath.stat().st_size
    bytes_read = 0

    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
            bytes_read += len(chunk)
            if progress_callback:
                progress_callback(bytes_read, file_size)

    return hasher.hexdigest()


def hash_stream(stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """
    Calculate XXHash64 of a binary stream.

    Args:
        stream: Binary file-like object
        chunk_size: Size of chunks to read

    Returns:
        Hex string of the hash
    """
    hasher = xxhash.xxh64()
    while chunk := stream.read(chunk_size):
        hasher.update(chunk)
    return hasher.hexdigest()


def hash_bytes(data: bytes) -> str:
    """
    Calculate XXHash64 of bytes.

    Args:
        data: Bytes to hash

    Returns:
        Hex string of the hash
    """
    return xxhash.xxh64(data).hexdigest()


def verify_hash(filepath: Path, expected_hash: str) -> bool:
    """
    Verify a file matches an expected hash.

    Args:
        filepath: Path to file to verify
        expected_hash: Expected XXHash64 hex string

    Returns:
        True if hash matches, False otherwise
    """
    actual_hash = hash_file(filepath)
    return actual_hash.lower() == expected_hash.lower()
