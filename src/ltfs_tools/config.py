"""
Configuration and platform detection for LTFS tools.
"""

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def detect_tape_device_linux() -> Optional[str]:
    """Auto-detect tape device on Linux using lsscsi -g."""
    try:
        result = subprocess.run(
            ["lsscsi", "-g"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "tape" in line.lower():
                    # Line format: [H:C:T:L]   tape    VENDOR   MODEL   REV   /dev/stN   /dev/sgN
                    parts = line.split()
                    # The sg device is typically the last column
                    for part in reversed(parts):
                        if part.startswith("/dev/sg"):
                            return part
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def detect_tape_device_macos() -> Optional[str]:
    """Auto-detect tape device on macOS by scanning IOKit."""
    try:
        # Use system_profiler to find tape devices
        result = subprocess.run(
            ["system_profiler", "SPSASDataType", "-xml"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Simple heuristic: look for tape devices
            # For now, default to "0" which is typical for first tape drive
            # A more robust implementation would parse the XML
            if "tape" in result.stdout.lower() or "ultrium" in result.stdout.lower():
                return "0"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def find_ltfs_binary_macos() -> Optional[Path]:
    """Find LTFS binary on macOS, checking multiple possible locations."""
    # Check if ltfs is in PATH first
    ltfs_path = shutil.which("ltfs")
    if ltfs_path:
        return Path(ltfs_path)

    # Check common framework locations
    frameworks = [
        "/Library/Frameworks/YoLTO.framework/Versions/Current/usr/bin/ltfs",
        "/Library/Frameworks/LTFS.framework/Versions/Current/usr/bin/ltfs",
    ]

    for fw_path in frameworks:
        path = Path(fw_path)
        if path.exists():
            return path

    return None


def find_mkltfs_binary_macos() -> Optional[Path]:
    """Find mkltfs binary on macOS, checking multiple possible locations."""
    # Check if mkltfs is in PATH first
    mkltfs_path = shutil.which("mkltfs")
    if mkltfs_path:
        return Path(mkltfs_path)

    # Check common framework locations
    frameworks = [
        "/Library/Frameworks/YoLTO.framework/Versions/Current/usr/bin/mkltfs",
        "/Library/Frameworks/LTFS.framework/Versions/Current/usr/bin/mkltfs",
    ]

    for fw_path in frameworks:
        path = Path(fw_path)
        if path.exists():
            return path

    return None


@dataclass
class PlatformConfig:
    """Platform-specific configuration."""

    name: str
    ltfs_bin: Path
    mkltfs_bin: Path
    default_mount_point: Path
    default_device: str

    def validate(self) -> list[str]:
        """Check if platform dependencies are available. Returns list of issues."""
        issues = []
        if not self.ltfs_bin.exists():
            issues.append(f"LTFS binary not found at {self.ltfs_bin}")
        if not shutil.which("rsync"):
            issues.append("rsync not found in PATH")
        return issues


def _get_macos_config() -> PlatformConfig:
    """Build macOS config with auto-detected binary paths."""
    # Try to find LTFS binaries
    ltfs_bin = find_ltfs_binary_macos()
    mkltfs_bin = find_mkltfs_binary_macos()

    # Fallback to IBM LTFS default if not found
    if ltfs_bin is None:
        ltfs_bin = Path("/Library/Frameworks/LTFS.framework/Versions/Current/usr/bin/ltfs")
    if mkltfs_bin is None:
        mkltfs_bin = Path("/Library/Frameworks/LTFS.framework/Versions/Current/usr/bin/mkltfs")

    return PlatformConfig(
        name="macos",
        ltfs_bin=ltfs_bin,
        mkltfs_bin=mkltfs_bin,
        default_mount_point=Path("/Volumes/LTFS"),
        default_device="0",
    )


MACOS_CONFIG = _get_macos_config()

LINUX_CONFIG = PlatformConfig(
    name="linux",
    ltfs_bin=Path("/usr/local/bin/ltfs"),
    mkltfs_bin=Path("/usr/local/bin/mkltfs"),
    default_mount_point=Path("/media/tape"),
    default_device="/dev/sg3",
)


def get_platform_config() -> PlatformConfig:
    """Detect platform and return appropriate configuration."""
    system = platform.system().lower()
    if system == "darwin":
        return MACOS_CONFIG
    elif system == "linux":
        return LINUX_CONFIG
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


@dataclass
class Config:
    """Main configuration for LTFS tools."""

    # Platform-specific settings
    platform: PlatformConfig = field(default_factory=get_platform_config)

    # Paths (can be overridden via environment or CLI)
    mount_point: Optional[Path] = None
    device: Optional[str] = None
    archive_base: Optional[Path] = None

    # LTFS options
    sync_type: str = "time@5"  # Sync index every 5 minutes (YoYotta uses "unmount")
    iosize: Optional[int] = None  # I/O buffer size in bytes (YoYotta uses 524288)
    rules: Optional[str] = None  # LTFS rules (e.g., "size=500k/name=metadata.xml")
    foreground: bool = False  # Run ltfs in foreground mode (-f flag)

    # Transfer options
    rsync_opts: list[str] = field(
        default_factory=lambda: ["-av", "--progress", "--itemize-changes"]
    )

    # Files/directories to exclude
    excludes: list[str] = field(
        default_factory=lambda: [
            ".DS_Store",
            "._*",  # AppleDouble files (macOS metadata)
            ".Spotlight-*",
            ".fseventsd",
            ".Trashes",
            ".Trash/",
            "*/Library/Caches/*",  # Match Library/Caches anywhere in path
            "*.tmp",
            ".TemporaryItems",
            "Thumbs.db",
        ]
    )

    def __post_init__(self):
        """Set defaults from platform config and environment."""
        if self.mount_point is None:
            env_mount = os.environ.get("LTFS_MOUNT_POINT")
            self.mount_point = Path(env_mount) if env_mount else self.platform.default_mount_point

        if self.device is None:
            env_device = os.environ.get("LTFS_DEVICE")
            if env_device:
                self.device = env_device
            elif self.platform.name == "linux":
                # Try auto-detection on Linux
                detected = detect_tape_device_linux()
                self.device = detected if detected else self.platform.default_device
            elif self.platform.name == "macos":
                # Try auto-detection on macOS
                detected = detect_tape_device_macos()
                self.device = detected if detected else self.platform.default_device
            else:
                self.device = self.platform.default_device

        if self.archive_base is None:
            env_base = os.environ.get("LTFS_ARCHIVE_BASE")
            self.archive_base = Path(env_base) if env_base else Path.home() / "ltfs-archives"

    @property
    def log_dir(self) -> Path:
        return self.archive_base / "logs"

    @property
    def mhl_dir(self) -> Path:
        return self.archive_base / "mhl"

    @property
    def catalog_dir(self) -> Path:
        return self.archive_base / "catalogs"

    @property
    def index_dir(self) -> Path:
        """Directory for storing LTFS index backups."""
        return self.archive_base / "indexes"

    def init_dirs(self) -> None:
        """Create output directories if they don't exist."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.mhl_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def is_mounted(self) -> bool:
        """Check if a tape is mounted at the mount point."""
        if not self.mount_point.exists():
            return False
        # Check if it's a mount point by comparing device IDs
        try:
            mount_stat = self.mount_point.stat()
            parent_stat = self.mount_point.parent.stat()
            return mount_stat.st_dev != parent_stat.st_dev
        except OSError:
            return False


# Global config instance (can be overridden in tests)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
