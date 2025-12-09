"""
Tests for LTFS tools.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ltfs_tools.hash import hash_bytes, hash_file
from ltfs_tools.mhl import MHL, CreatorInfo, HashEntry, TapeInfo


class TestHash:
    """Tests for hashing functions."""

    def test_hash_bytes(self):
        """Test hashing bytes."""
        result = hash_bytes(b"hello world")
        assert isinstance(result, str)
        assert len(result) == 16  # XXHash64 produces 16 hex chars

    def test_hash_bytes_consistent(self):
        """Test that same input produces same hash."""
        data = b"test data 12345"
        assert hash_bytes(data) == hash_bytes(data)

    def test_hash_file(self):
        """Test hashing a file."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test file content")
            f.flush()

            result = hash_file(Path(f.name))
            assert isinstance(result, str)
            assert len(result) == 16

            # Should match hashing the same bytes
            assert result == hash_bytes(b"test file content")


class TestMHL:
    """Tests for MHL file handling."""

    def test_create_mhl(self):
        """Test creating an MHL file."""
        mhl = MHL()
        assert mhl.version == "1.1"
        assert len(mhl.hashes) == 0

    def test_add_hash_entry(self):
        """Test adding a hash entry."""
        mhl = MHL()
        entry = HashEntry(
            file="test/file.txt",
            size=1234,
            xxhash64be="abc123def456789",
            last_modification_date=datetime.now(timezone.utc),
        )
        mhl.add_hash(entry)
        assert len(mhl) == 1

    def test_mhl_to_xml(self):
        """Test converting MHL to XML."""
        mhl = MHL(
            creator_info=CreatorInfo(
                name="Test User",
                username="testuser",
                hostname="testhost",
                tool="pytest",
            ),
            tape_info=TapeInfo(name="TEST01"),
        )
        mhl.add_hash(
            HashEntry(
                file="test.txt",
                size=100,
                xxhash64be="1234567890abcdef",
            )
        )

        xml = mhl.to_xml()
        assert '<?xml version="1.0"' in xml
        assert "<hashlist" in xml
        assert "TEST01" in xml
        assert "test.txt" in xml
        assert "1234567890abcdef" in xml

    def test_mhl_save_and_load(self):
        """Test saving and loading MHL files."""
        mhl = MHL(tape_info=TapeInfo(name="TEST01"))
        mhl.add_hash(
            HashEntry(
                file="path/to/file.txt",
                size=999,
                xxhash64be="fedcba0987654321",
            )
        )

        with tempfile.NamedTemporaryFile(suffix=".mhl", delete=False) as f:
            path = Path(f.name)

        try:
            mhl.save(path)
            assert path.exists()

            loaded = MHL.load(path)
            assert loaded.tape_info.name == "TEST01"
            assert len(loaded) == 1
            assert loaded.hashes[0].file == "path/to/file.txt"
            assert loaded.hashes[0].xxhash64be == "fedcba0987654321"
        finally:
            path.unlink(missing_ok=True)


class TestHashEntry:
    """Tests for HashEntry."""

    def test_entry_to_element(self):
        """Test converting entry to XML element."""
        entry = HashEntry(
            file="test.txt",
            size=100,
            xxhash64be="abcdef1234567890",
        )
        elem = entry.to_element()
        assert elem.tag == "hash"
        assert elem.find("file").text == "test.txt"
        assert elem.find("size").text == "100"
        assert elem.find("xxhash64be").text == "abcdef1234567890"
