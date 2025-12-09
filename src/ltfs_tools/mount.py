"""
LTFS mount and unmount operations.
"""

import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any

from .config import Config, get_config


class MountError(Exception):
    """Error during mount/unmount operations."""

    pass


def mount(
    volume_name: Optional[str] = None,
    mount_point: Optional[Path] = None,
    device: Optional[str] = None,
    config: Optional[Config] = None,
) -> Path:
    """
    Mount an LTFS-formatted tape.

    Args:
        volume_name: Optional display name for the volume
        mount_point: Mount location (default from config)
        device: Tape device (default from config)
        config: Configuration to use (default: global config)

    Returns:
        Path to mount point

    Raises:
        MountError: If mount fails
    """
    if config is None:
        config = get_config()

    mount_point = mount_point or config.mount_point
    device = device or config.device

    # Check if already mounted
    if config.is_mounted():
        raise MountError(f"Tape already mounted at {mount_point}")

    # Check LTFS binary exists
    if not config.platform.ltfs_bin.exists():
        raise MountError(
            f"LTFS binary not found at {config.platform.ltfs_bin}. "
            "Make sure LTFS drivers are installed."
        )

    # Create mount point if needed
    mount_point.mkdir(parents=True, exist_ok=True)

    # Create index backup directory
    config.init_dirs()

    # Build mount command
    cmd = [
        str(config.platform.ltfs_bin),
        str(mount_point),
        "-o", f"devname={device}",
        "-o", f"sync_type={config.sync_type}",
        "-o", f"capture_index={config.index_dir}",  # Auto-backup indexes
    ]

    if volume_name:
        cmd.extend(["-o", f"volname={volume_name}"])

    # Optional LTFS settings (YoYotta-style)
    if config.iosize:
        cmd.extend(["-o", f"iosize={config.iosize}"])

    if config.rules:
        cmd.extend(["-o", f"rules={config.rules}"])

    # Foreground mode (YoYotta uses -f)
    if config.foreground:
        cmd.append("-f")

    # Execute mount
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout for tape load
        )

        if result.returncode != 0:
            raise MountError(f"Mount failed: {result.stderr}")

    except subprocess.TimeoutExpired:
        raise MountError("Mount timed out - tape may be stuck loading")
    except FileNotFoundError:
        raise MountError(f"LTFS binary not found: {config.platform.ltfs_bin}")

    # Wait for mount and verify
    time.sleep(2)
    if not _verify_mount(mount_point):
        raise MountError("Mount command succeeded but mount point is not accessible")

    return mount_point


def unmount(
    mount_point: Optional[Path] = None,
    config: Optional[Config] = None,
) -> None:
    """
    Safely unmount an LTFS tape.

    This writes the final index to tape - always unmount before ejecting!

    Args:
        mount_point: Mount location (default from config)
        config: Configuration to use (default: global config)

    Raises:
        MountError: If unmount fails
    """
    if config is None:
        config = get_config()

    mount_point = mount_point or config.mount_point

    # Check if mounted
    if not config.is_mounted():
        return  # Already unmounted, nothing to do

    # Sync filesystem
    subprocess.run(["sync"], check=False)

    # Unmount
    if config.platform.name == "macos":
        cmd = ["umount", str(mount_point)]
    else:
        # Try fusermount first (for FUSE-based LTFS), fall back to umount
        cmd = ["fusermount", "-u", str(mount_point)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            # On Linux, try regular umount as fallback
            if config.platform.name == "linux":
                result = subprocess.run(
                    ["umount", str(mount_point)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            if result.returncode != 0:
                raise MountError(
                    f"Unmount failed: {result.stderr}\n"
                    f"Check for processes using the mount point with: lsof +D {mount_point}"
                )

    except subprocess.TimeoutExpired:
        raise MountError(
            "Unmount timed out - tape may be busy writing index. "
            "Do NOT eject the tape!"
        )

    # Verify unmount
    time.sleep(2)
    if _verify_mount(mount_point):
        raise MountError("Unmount command succeeded but mount point is still mounted")


def _verify_mount(mount_point: Path) -> bool:
    """Check if mount point is actually mounted."""
    if not mount_point.exists():
        return False

    try:
        # Check if it's a mount point by comparing device IDs
        mount_stat = mount_point.stat()
        parent_stat = mount_point.parent.stat()
        return mount_stat.st_dev != parent_stat.st_dev
    except OSError:
        return False


def get_tape_attributes(mount_point: Path) -> Dict[str, Any]:
    """
    Get LTFS tape attributes from extended attributes.

    Returns dictionary with tape metadata like volume name, barcode, etc.
    """
    import platform

    # Extended attribute prefix depends on platform
    if platform.system().lower() == "linux":
        prefix = "user.ltfs."
    else:  # macOS/Darwin
        prefix = "ltfs."

    attributes = {}

    # Try to read LTFS extended attributes
    try:
        # Try using xattr module if available
        try:
            import xattr

            # Common LTFS attributes
            attr_names = [
                "volumeName",
                "volumeUUID",
                "vendor",
                "version",
                "generation",
                "formatTime",
                "updateTime",
                "softwareProduct",
                "softwareVendor",
                "softwareVersion",
                "softwareFormatSpec",
                "barcode",
                "mediaPool",
            ]

            for attr in attr_names:
                try:
                    value = xattr.getxattr(str(mount_point), prefix + attr)
                    if isinstance(value, bytes):
                        value = value.decode('utf-8', errors='ignore').strip('\x00')
                    attributes[attr] = value
                except (OSError, KeyError):
                    pass

        except ImportError:
            # Fall back to using getfattr command on Linux
            if platform.system().lower() == "linux":
                result = subprocess.run(
                    ["getfattr", "-d", "-m", "user.ltfs", str(mount_point)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if result.returncode == 0:
                    # Parse getfattr output
                    for line in result.stdout.splitlines():
                        if "=" in line and line.startswith("user.ltfs."):
                            key, value = line.split("=", 1)
                            key = key.replace("user.ltfs.", "").strip()
                            value = value.strip().strip('"')
                            attributes[key] = value
    except Exception:
        pass

    return attributes


def get_tape_info(mount_point: Optional[Path] = None, config: Optional[Config] = None, deep_scan: bool = False) -> dict:
    """
    Get information about a mounted tape.

    Args:
        mount_point: Mount location (default from config)
        config: Configuration to use (default: global config)
        deep_scan: If True, recursively count all files (slow for large tapes)

    Returns:
        Dictionary with tape information
    """
    if config is None:
        config = get_config()

    mount_point = mount_point or config.mount_point

    if not _verify_mount(mount_point):
        return {"mounted": False}

    # Get tape attributes from extended attributes
    tape_attrs = get_tape_attributes(mount_point)

    if deep_scan:
        # Slow but accurate - count all files recursively
        file_count = 0
        dir_count = 0
        total_size = 0

        for item in mount_point.rglob("*"):
            try:
                if item.is_file():
                    file_count += 1
                    try:
                        total_size += item.stat().st_size
                    except OSError:
                        pass
                elif item.is_dir():
                    dir_count += 1
            except (OSError, PermissionError):
                # Skip files/dirs we can't access
                pass

        return {
            "mounted": True,
            "mount_point": str(mount_point),
            "file_count": file_count,
            "dir_count": dir_count,
            "total_size": total_size,
            **tape_attrs,
        }
    else:
        # Fast but approximate - use du command and list top-level only
        import shutil

        # Get disk usage using du (much faster than Python recursion)
        total_size = 0
        try:
            result = subprocess.run(
                ["du", "-sb", str(mount_point)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                total_size = int(result.stdout.split()[0])
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            pass

        # Count top-level items only
        top_level_dirs = []
        top_level_files = []
        try:
            for item in mount_point.iterdir():
                if item.is_dir():
                    top_level_dirs.append(item.name)
                elif item.is_file():
                    top_level_files.append(item.name)
        except (OSError, PermissionError):
            pass

        return {
            "mounted": True,
            "mount_point": str(mount_point),
            "total_size": total_size,
            "top_level_dirs": top_level_dirs,
            "top_level_files": top_level_files,
            **tape_attrs,
        }
