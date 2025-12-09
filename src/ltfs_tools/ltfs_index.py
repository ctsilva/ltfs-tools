"""
LTFS index parsing and manipulation.

Parses LTFS index XML files to extract filesystem metadata.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class FileExtent:
    """Physical location of file data on tape."""
    partition: str  # 'a' or 'b'
    start_block: int
    byte_offset: int
    byte_count: int


@dataclass
class IndexFile:
    """File metadata from LTFS index."""
    name: str
    path: str  # Full path from root
    size: int
    modify_time: Optional[datetime]
    create_time: Optional[datetime]
    change_time: Optional[datetime]
    access_time: Optional[datetime]
    readonly: bool
    extents: List[FileExtent]
    uid: Optional[str] = None  # File UID for deduplication


@dataclass
class IndexDirectory:
    """Directory metadata from LTFS index."""
    name: str
    path: str  # Full path from root
    modify_time: Optional[datetime]
    create_time: Optional[datetime]
    change_time: Optional[datetime]
    access_time: Optional[datetime]
    readonly: bool
    files: List[IndexFile]
    subdirs: List['IndexDirectory']


@dataclass
class LTFSIndex:
    """Parsed LTFS index."""
    version: str
    volume_uuid: str
    generation: int
    update_time: Optional[datetime]
    location: str  # Partition where index was read from
    creator: str
    comment: Optional[str]
    root: IndexDirectory


class LTFSIndexParser:
    """Parse LTFS index XML files."""

    # LTFS XML namespace
    NS = {'ltfs': 'http://www.ibm.com/xmlns/ltfs'}

    @staticmethod
    def parse_time(time_str: Optional[str]) -> Optional[datetime]:
        """Parse LTFS timestamp to datetime."""
        if not time_str:
            return None
        try:
            # LTFS uses ISO 8601 format: 2025-12-06T15:30:00Z
            return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None

    @classmethod
    def parse_file(cls, file_elem: ET.Element, parent_path: str) -> IndexFile:
        """Parse file element from index."""
        name = file_elem.findtext('ltfs:name', namespaces=cls.NS, default='')
        full_path = f"{parent_path}/{name}".replace('//', '/')

        size = int(file_elem.findtext('ltfs:length', namespaces=cls.NS, default='0'))
        readonly = file_elem.findtext('ltfs:readonly', namespaces=cls.NS, default='false') == 'true'
        uid = file_elem.findtext('ltfs:fileuid', namespaces=cls.NS)

        # Parse timestamps
        modify_time = cls.parse_time(file_elem.findtext('ltfs:modifytime', namespaces=cls.NS))
        create_time = cls.parse_time(file_elem.findtext('ltfs:creationtime', namespaces=cls.NS))
        change_time = cls.parse_time(file_elem.findtext('ltfs:changetime', namespaces=cls.NS))
        access_time = cls.parse_time(file_elem.findtext('ltfs:accesstime', namespaces=cls.NS))

        # Parse extents (physical locations)
        extents = []
        for extent_elem in file_elem.findall('ltfs:extentinfo', namespaces=cls.NS):
            partition = extent_elem.findtext('ltfs:partition', namespaces=cls.NS, default='b')
            start_block = int(extent_elem.findtext('ltfs:startblock', namespaces=cls.NS, default='0'))
            byte_offset = int(extent_elem.findtext('ltfs:byteoffset', namespaces=cls.NS, default='0'))
            byte_count = int(extent_elem.findtext('ltfs:bytecount', namespaces=cls.NS, default='0'))

            extents.append(FileExtent(
                partition=partition,
                start_block=start_block,
                byte_offset=byte_offset,
                byte_count=byte_count
            ))

        return IndexFile(
            name=name,
            path=full_path,
            size=size,
            modify_time=modify_time,
            create_time=create_time,
            change_time=change_time,
            access_time=access_time,
            readonly=readonly,
            extents=extents,
            uid=uid
        )

    @classmethod
    def parse_directory(cls, dir_elem: ET.Element, parent_path: str = '') -> IndexDirectory:
        """Parse directory element from index (recursive)."""
        name = dir_elem.findtext('ltfs:name', namespaces=cls.NS, default='')
        if parent_path:
            full_path = f"{parent_path}/{name}".replace('//', '/')
        else:
            full_path = '/'

        readonly = dir_elem.findtext('ltfs:readonly', namespaces=cls.NS, default='false') == 'true'

        # Parse timestamps
        modify_time = cls.parse_time(dir_elem.findtext('ltfs:modifytime', namespaces=cls.NS))
        create_time = cls.parse_time(dir_elem.findtext('ltfs:creationtime', namespaces=cls.NS))
        change_time = cls.parse_time(dir_elem.findtext('ltfs:changetime', namespaces=cls.NS))
        access_time = cls.parse_time(dir_elem.findtext('ltfs:accesstime', namespaces=cls.NS))

        # Parse contents
        files = []
        subdirs = []

        contents_elem = dir_elem.find('ltfs:contents', namespaces=cls.NS)
        if contents_elem is not None:
            # Parse files
            for file_elem in contents_elem.findall('ltfs:file', namespaces=cls.NS):
                files.append(cls.parse_file(file_elem, full_path))

            # Parse subdirectories (recursive)
            for subdir_elem in contents_elem.findall('ltfs:directory', namespaces=cls.NS):
                subdirs.append(cls.parse_directory(subdir_elem, full_path))

        return IndexDirectory(
            name=name,
            path=full_path,
            modify_time=modify_time,
            create_time=create_time,
            change_time=change_time,
            access_time=access_time,
            readonly=readonly,
            files=files,
            subdirs=subdirs
        )

    @classmethod
    def parse(cls, index_file: Path) -> LTFSIndex:
        """Parse an LTFS index XML file."""
        tree = ET.parse(index_file)
        root_elem = tree.getroot()

        # Parse index metadata
        version = root_elem.get('version', 'unknown')
        volume_uuid = root_elem.findtext('ltfs:volumeuuid', namespaces=cls.NS, default='')
        generation = int(root_elem.findtext('ltfs:generationnumber', namespaces=cls.NS, default='0'))
        update_time = cls.parse_time(root_elem.findtext('ltfs:updatetime', namespaces=cls.NS))
        location = root_elem.findtext('ltfs:location', namespaces=cls.NS, default='')
        comment = root_elem.findtext('ltfs:comment', namespaces=cls.NS)

        # Parse creator
        creator_elem = root_elem.find('ltfs:creator', namespaces=cls.NS)
        if creator_elem is not None:
            creator = creator_elem.text or 'unknown'
        else:
            creator = 'unknown'

        # Parse directory tree
        directory_elem = root_elem.find('ltfs:directory', namespaces=cls.NS)
        if directory_elem is None:
            raise ValueError("No root directory found in index")

        root_dir = cls.parse_directory(directory_elem)

        return LTFSIndex(
            version=version,
            volume_uuid=volume_uuid,
            generation=generation,
            update_time=update_time,
            location=location,
            creator=creator,
            comment=comment,
            root=root_dir
        )

    @classmethod
    def get_all_files(cls, index: LTFSIndex) -> List[IndexFile]:
        """Get flat list of all files in index."""
        files = []

        def collect_files(directory: IndexDirectory):
            files.extend(directory.files)
            for subdir in directory.subdirs:
                collect_files(subdir)

        collect_files(index.root)
        return files

    @classmethod
    def get_all_directories(cls, index: LTFSIndex) -> List[IndexDirectory]:
        """Get flat list of all directories in index."""
        directories = []

        def collect_dirs(directory: IndexDirectory):
            directories.append(directory)
            for subdir in directory.subdirs:
                collect_dirs(subdir)

        collect_dirs(index.root)
        return directories
