"""
Media Hash List (MHL) file handling.

MHL is an industry-standard XML format for storing file hashes,
commonly used in film/TV production for verifying media transfers.
"""

import getpass
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from xml.dom import minidom

from .utils import normalize_path

__version__ = "0.1.0"


def sanitize_xml_string(s: str) -> str:
    """
    Remove characters that are invalid in XML 1.0.

    XML 1.0 allows: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
    This function removes any characters outside this range.
    """
    # Pattern matches invalid XML 1.0 characters
    # Control chars 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, and surrogates 0xD800-0xDFFF, 0xFFFE, 0xFFFF
    invalid_xml_chars = re.compile(
        '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]'
    )
    return invalid_xml_chars.sub('', s)


@dataclass
class HashEntry:
    """A single file hash entry in an MHL file."""

    file: str  # Relative path
    size: int  # File size in bytes
    xxhash64be: str  # XXHash64 big-endian hex string
    last_modification_date: Optional[datetime] = None
    hash_date: Optional[datetime] = None

    def to_element(self) -> ET.Element:
        """Convert to XML element."""
        hash_elem = ET.Element("hash")

        file_elem = ET.SubElement(hash_elem, "file")
        # Normalize to NFC for cross-platform consistency
        file_elem.text = sanitize_xml_string(normalize_path(self.file))

        size_elem = ET.SubElement(hash_elem, "size")
        size_elem.text = str(self.size)

        if self.last_modification_date:
            mod_elem = ET.SubElement(hash_elem, "lastmodificationdate")
            mod_elem.text = self.last_modification_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        xxhash_elem = ET.SubElement(hash_elem, "xxhash64be")
        xxhash_elem.text = self.xxhash64be

        if self.hash_date:
            hash_date_elem = ET.SubElement(hash_elem, "hashdate")
            hash_date_elem.text = self.hash_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        return hash_elem

    @classmethod
    def from_element(cls, elem: ET.Element) -> "HashEntry":
        """Create from XML element."""
        # Normalize to NFC for cross-platform consistency
        file_path = normalize_path(elem.findtext("file", ""))
        size = int(elem.findtext("size", "0"))
        xxhash = elem.findtext("xxhash64be", "")

        mod_date_str = elem.findtext("lastmodificationdate")
        mod_date = None
        if mod_date_str:
            try:
                mod_date = datetime.strptime(mod_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        hash_date_str = elem.findtext("hashdate")
        hash_date = None
        if hash_date_str:
            try:
                hash_date = datetime.strptime(hash_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        return cls(
            file=file_path,
            size=size,
            xxhash64be=xxhash,
            last_modification_date=mod_date,
            hash_date=hash_date,
        )


@dataclass
class CreatorInfo:
    """Information about who/what created the MHL file."""

    name: str = ""
    username: str = ""
    hostname: str = ""
    tool: str = ""
    start_date: Optional[datetime] = None
    finish_date: Optional[datetime] = None

    @classmethod
    def default(cls) -> "CreatorInfo":
        """Create with current system defaults."""
        try:
            # Try to get full name (works on macOS)
            import pwd

            name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
        except Exception:
            name = getpass.getuser()

        return cls(
            name=name,
            username=getpass.getuser(),
            hostname=socket.gethostname(),
            tool=f"ltfs-tools {__version__}",
            start_date=datetime.now(timezone.utc),
        )

    def to_element(self) -> ET.Element:
        """Convert to XML element."""
        creator = ET.Element("creatorinfo")

        if self.name:
            name_elem = ET.SubElement(creator, "name")
            name_elem.text = self.name

        if self.username:
            user_elem = ET.SubElement(creator, "username")
            user_elem.text = self.username

        if self.hostname:
            host_elem = ET.SubElement(creator, "hostname")
            host_elem.text = self.hostname

        if self.tool:
            tool_elem = ET.SubElement(creator, "tool")
            tool_elem.text = self.tool

        if self.start_date:
            start_elem = ET.SubElement(creator, "startdate")
            start_elem.text = self.start_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        if self.finish_date:
            finish_elem = ET.SubElement(creator, "finishdate")
            finish_elem.text = self.finish_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        return creator

    @classmethod
    def from_element(cls, elem: ET.Element) -> "CreatorInfo":
        """Create from XML element."""
        info = cls()
        info.name = elem.findtext("name", "")
        info.username = elem.findtext("username", "")
        info.hostname = elem.findtext("hostname", "")
        info.tool = elem.findtext("tool", "")

        start_str = elem.findtext("startdate")
        if start_str:
            try:
                info.start_date = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        finish_str = elem.findtext("finishdate")
        if finish_str:
            try:
                info.finish_date = datetime.strptime(finish_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        return info


@dataclass
class TapeInfo:
    """Information about the tape."""

    name: str = ""
    serial: str = ""
    vendor: str = ""
    product: str = ""

    def to_element(self) -> ET.Element:
        """Convert to XML element."""
        tape = ET.Element("tapeinfo")

        if self.name:
            name_elem = ET.SubElement(tape, "name")
            name_elem.text = self.name

        if self.serial:
            serial_elem = ET.SubElement(tape, "serial")
            serial_elem.text = self.serial

        if self.vendor:
            vendor_elem = ET.SubElement(tape, "vendor")
            vendor_elem.text = self.vendor

        if self.product:
            product_elem = ET.SubElement(tape, "product")
            product_elem.text = self.product

        return tape

    @classmethod
    def from_element(cls, elem: ET.Element) -> "TapeInfo":
        """Create from XML element."""
        return cls(
            name=elem.findtext("name", ""),
            serial=elem.findtext("serial", ""),
            vendor=elem.findtext("vendor", ""),
            product=elem.findtext("product", ""),
        )


@dataclass
class MHL:
    """A Media Hash List file."""

    version: str = "1.1"
    creator_info: CreatorInfo = field(default_factory=CreatorInfo.default)
    tape_info: Optional[TapeInfo] = None
    hashes: list[HashEntry] = field(default_factory=list)

    def add_hash(self, entry: HashEntry) -> None:
        """Add a hash entry."""
        self.hashes.append(entry)

    def to_xml(self, pretty: bool = True) -> str:
        """
        Convert to XML string.

        Args:
            pretty: If True, format with indentation

        Returns:
            XML string
        """
        root = ET.Element("hashlist", version=self.version)

        root.append(self.creator_info.to_element())

        if self.tape_info:
            root.append(self.tape_info.to_element())

        for hash_entry in self.hashes:
            root.append(hash_entry.to_element())

        if pretty:
            xml_str = ET.tostring(root, encoding="unicode")
            try:
                dom = minidom.parseString(xml_str)
                return dom.toprettyxml(indent="    ", encoding=None)
            except Exception as e:
                # If pretty printing fails, try to identify the problematic entry
                # by attempting to parse progressively larger subsets
                import sys
                print(f"Warning: XML pretty-print failed: {e}", file=sys.stderr)
                print("Attempting to identify problematic entry...", file=sys.stderr)

                # Try each hash entry individually to find the bad one
                for i, entry in enumerate(self.hashes):
                    test_root = ET.Element("test")
                    test_root.append(entry.to_element())
                    test_str = ET.tostring(test_root, encoding="unicode")
                    try:
                        minidom.parseString(test_str)
                    except Exception:
                        print(f"Problematic entry #{i}: {entry.file!r}", file=sys.stderr)

                # Fall back to non-pretty output
                return ET.tostring(root, encoding="unicode", xml_declaration=True)
        else:
            return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def save(self, filepath: Path) -> None:
        """Save MHL to file."""
        xml_content = self.to_xml(pretty=True)
        filepath.write_text(xml_content, encoding="utf-8")

    @classmethod
    def load(cls, filepath: Path) -> "MHL":
        """Load MHL from file."""
        tree = ET.parse(filepath)
        root = tree.getroot()

        mhl = cls(version=root.get("version", "1.1"))

        creator_elem = root.find("creatorinfo")
        if creator_elem is not None:
            mhl.creator_info = CreatorInfo.from_element(creator_elem)

        tape_elem = root.find("tapeinfo")
        if tape_elem is not None:
            mhl.tape_info = TapeInfo.from_element(tape_elem)

        for hash_elem in root.findall("hash"):
            mhl.hashes.append(HashEntry.from_element(hash_elem))

        return mhl

    def __iter__(self) -> Iterator[HashEntry]:
        """Iterate over hash entries."""
        return iter(self.hashes)

    def __len__(self) -> int:
        """Return number of hash entries."""
        return len(self.hashes)


# Need this import for CreatorInfo.default()
import os
