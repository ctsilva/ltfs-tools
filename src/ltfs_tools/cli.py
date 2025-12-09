"""
Command-line interface for LTFS tools.
"""

from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from . import catalog as catalog_module
from .config import get_config
from .mount import mount as mount_func, unmount as unmount_func, format_tape as format_tape_func, get_tape_info, MountError
from .transfer import transfer as transfer_func, TransferError
from .verify import verify as verify_func, VerifyError

console = Console()


def format_bytes(size: int) -> str:
    """Format bytes to human readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


@click.group()
@click.version_option(version="0.1.0")
def main():
    """LTFS Tools - Cross-platform LTO tape archive management."""
    pass


@main.command()
@click.argument("volume_name")
@click.option(
    "-d", "--device",
    help="Tape device to format",
)
@click.option(
    "-f", "--force",
    is_flag=True,
    help="Force format even if tape is already formatted",
)
@click.option(
    "--no-compression",
    is_flag=True,
    help="Disable hardware compression (enabled by default)",
)
@click.option(
    "--rules",
    help="Data placement rules (e.g., 'size=500k/name=metadata.xml')",
)
def format(volume_name: str, device: Optional[str], force: bool, no_compression: bool, rules: Optional[str]):
    """Format a tape with LTFS filesystem.

    WARNING: This will erase all data on the tape!

    Examples:
        ltfs-tool format BACKUP01
        ltfs-tool format BACKUP01 -d 0
        ltfs-tool format BACKUP01 --force
        ltfs-tool format BACKUP01 --rules "size=500k/name=*.mhl"
        ltfs-tool format BACKUP01 --no-compression
    """
    config = get_config()

    console.print("[bold red]WARNING: This will erase all data on the tape![/bold red]")
    console.print(f"Volume name: {volume_name}")
    console.print(f"Device: {device or config.device}")
    if force:
        console.print("Force: YES (will overwrite existing LTFS format)")
    if no_compression:
        console.print("Compression: DISABLED")
    else:
        console.print("Compression: ENABLED (hardware)")
    if rules:
        console.print(f"Rules: {rules}")
    console.print()

    if not click.confirm("Are you sure you want to format this tape?"):
        console.print("Format cancelled")
        return

    console.print("[bold]Formatting tape with LTFS...[/bold]")
    console.print("This may take several minutes...")

    try:
        format_tape_func(
            volume_name=volume_name,
            device=device,
            compression=not no_compression,
            rules=rules,
            force=force,
            config=config,
        )
        console.print(f"[green]✓[/green] Tape formatted successfully as '{volume_name}'")
        console.print("  You can now mount and use the tape")

    except MountError as e:
        console.print(f"[red]✗[/red] Format failed: {e}")
        raise SystemExit(1)


@main.command()
@click.argument("volume_name", required=False)
@click.option(
    "-m", "--mount-point",
    type=click.Path(path_type=Path),
    help="Mount location",
)
@click.option(
    "-d", "--device",
    help="Tape device",
)
@click.option(
    "--sync-type",
    help="Index sync strategy (e.g., 'time@5' or 'unmount')",
)
@click.option(
    "--iosize",
    type=int,
    help="I/O buffer size in bytes (e.g., 524288 for 512KB)",
)
@click.option(
    "--rules",
    help="LTFS rules (e.g., 'size=500k/name=metadata.xml')",
)
@click.option(
    "-f", "--foreground",
    is_flag=True,
    help="Run LTFS in foreground mode",
)
def mount(
    volume_name: Optional[str],
    mount_point: Optional[Path],
    device: Optional[str],
    sync_type: Optional[str],
    iosize: Optional[int],
    rules: Optional[str],
    foreground: bool,
):
    """Mount an LTFS-formatted tape."""
    config = get_config()

    # Apply CLI overrides to config
    if sync_type:
        config.sync_type = sync_type
    if iosize:
        config.iosize = iosize
    if rules:
        config.rules = rules
    if foreground:
        config.foreground = foreground

    console.print("[bold]Mounting LTFS tape...[/bold]")

    try:
        result = mount_func(
            volume_name=volume_name,
            mount_point=mount_point,
            device=device,
            config=config,
        )
        console.print(f"[green]✓[/green] Tape mounted at {result}")

        # Show basic info
        info = get_tape_info(result, config)
        if info.get("mounted"):
            # file_count only available with deep_scan
            if "file_count" in info:
                console.print(f"  Files: {info['file_count']}")
            console.print(f"  Size: {format_bytes(info['total_size'])}")

    except MountError as e:
        console.print(f"[red]✗[/red] Mount failed: {e}")
        raise SystemExit(1)


@main.command()
@click.option(
    "-m", "--mount-point",
    type=click.Path(path_type=Path),
    help="Mount location",
)
def unmount(mount_point: Optional[Path]):
    """Safely unmount an LTFS tape.

    Always unmount before ejecting the tape to ensure the index is written.
    """
    config = get_config()

    console.print("[bold]Unmounting LTFS tape...[/bold]")
    console.print("Writing final index to tape (this may take a moment)...")

    try:
        unmount_func(mount_point=mount_point, config=config)
        console.print("[green]✓[/green] Tape unmounted successfully")
        console.print("  It is now safe to eject the tape")

    except MountError as e:
        console.print(f"[red]✗[/red] Unmount failed: {e}")
        raise SystemExit(1)


@main.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("tape_name", required=False)
@click.option(
    "-n", "--dry-run",
    is_flag=True,
    help="Show what would be transferred without copying",
)
@click.option(
    "--no-verify",
    is_flag=True,
    help="Skip verification after transfer",
)
@click.option(
    "-m", "--mount-point",
    type=click.Path(path_type=Path),
    help="Mount location (default: auto-detect)",
)
def transfer(
    source: Path,
    tape_name: Optional[str],
    dry_run: bool,
    no_verify: bool,
    mount_point: Optional[Path],
):
    """Transfer files to an LTFS tape with verification.

    SOURCE is the directory or file to archive.
    TAPE_NAME is used for logs and MHL files (default: derived from mount point).
    """
    config = get_config()

    # Override mount point if specified
    if mount_point:
        config.mount_point = mount_point

    try:
        result = transfer_func(
            source=source,
            tape_name=tape_name,
            dry_run=dry_run,
            verify=not no_verify,
            config=config,
        )

        # Print summary
        console.print()
        console.print("[bold]Transfer Summary[/bold]")

        table = Table(show_header=False, box=None)
        table.add_column(style="dim")
        table.add_column()

        table.add_row("Source", str(result.source))
        table.add_row("Destination", str(result.destination))
        table.add_row("Files", str(result.files_total))
        table.add_row("Size", format_bytes(result.bytes_total))
        table.add_row("Duration", f"{result.duration_seconds:.1f}s")
        table.add_row("Verified", str(result.files_verified))
        table.add_row("Failed", str(result.files_failed))

        if result.log_path:
            table.add_row("Log", str(result.log_path))
        if result.mhl_path:
            table.add_row("MHL", str(result.mhl_path))
        if result.catalog_path:
            table.add_row("Catalog", str(result.catalog_path))

        console.print(table)

        # Print phase performance
        console.print()
        console.print("[bold]Phase Performance[/bold]")

        perf_table = Table(show_header=False, box=None)
        perf_table.add_column(style="dim")
        perf_table.add_column(style="cyan", justify="right")
        perf_table.add_column(style="green", justify="right")

        perf_table.add_row(
            "Phase 1 (Hash source)",
            f"{result.phase1_duration:6.2f}s",
            f"{result.phase1_throughput:6.1f} MB/s" if result.phase1_duration > 0 else "—"
        )
        perf_table.add_row(
            "Phase 2 (Transfer)",
            f"{result.phase2_duration:6.2f}s",
            f"{result.phase2_throughput:6.1f} MB/s" if result.phase2_duration > 0 else "—"
        )
        if result.phase3_duration > 0:
            perf_table.add_row(
                "Phase 3 (Verify)",
                f"{result.phase3_duration:6.2f}s",
                f"{result.phase3_throughput:6.1f} MB/s"
            )
        perf_table.add_row(
            "Phase 4 (MHL)",
            f"{result.phase4_duration:6.2f}s",
            "—"
        )
        perf_table.add_row(
            "Phase 5 (Catalog)",
            f"{result.phase5_duration:6.2f}s",
            "—"
        )

        # Add overall throughput
        total_mb = result.bytes_total / (1024 * 1024)
        overall_throughput = total_mb / result.duration_seconds if result.duration_seconds > 0 else 0
        perf_table.add_row(
            "[bold]Overall[/bold]",
            f"[bold]{result.duration_seconds:6.2f}s[/bold]",
            f"[bold]{overall_throughput:6.1f} MB/s[/bold]"
        )

        console.print(perf_table)

        if result.success:
            console.print("[green]✓[/green] Transfer completed successfully")
        else:
            console.print(f"[red]✗[/red] Transfer completed with {result.files_failed} failures")
            for f in result.failed_files[:10]:
                console.print(f"  - {f}")
            if len(result.failed_files) > 10:
                console.print(f"  ... and {len(result.failed_files) - 10} more")
            raise SystemExit(1)

    except TransferError as e:
        console.print(f"[red]✗[/red] Transfer failed: {e}")
        raise SystemExit(1)


@main.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("tape_name", required=False)
def recover(source: Path, tape_name: Optional[str]):
    """Recover MHL and catalog after a failed transfer.

    Re-hashes source files (fast SSD) and generates MHL/catalog.
    Use this when a transfer crashed during Phase 4 (MHL) or Phase 5 (Catalog)
    but the files are already on tape.

    SOURCE is the original source directory (same as used for transfer).
    TAPE_NAME is the tape/destination name (default: source directory name).

    Example:
        ltfs-tool recover /scratch/csilva/deathstar2 deathstar2
    """
    from datetime import datetime, timezone
    from rich.progress import (
        Progress, SpinnerColumn, TextColumn, BarColumn,
        TimeElapsedColumn, TransferSpeedColumn, DownloadColumn,
    )
    from .hash import hash_file
    from .mhl import MHL, CreatorInfo, HashEntry, TapeInfo
    from .transfer import normalize_path, _should_exclude

    config = get_config()
    tape_name = tape_name or source.name
    source_name = source.name

    if not config.is_mounted():
        console.print(f"[red]✗[/red] No tape mounted at {config.mount_point}")
        raise SystemExit(1)

    dest_dir = config.mount_point / source_name
    if not dest_dir.exists():
        console.print(f"[red]✗[/red] Directory not found on tape: {dest_dir}")
        console.print("  Make sure the transfer completed before running recover.")
        raise SystemExit(1)

    console.print(f"[bold]Recovering transfer:[/bold] {source_name}")
    console.print(f"  Source: {source}")
    console.print(f"  Tape directory: {dest_dir}")

    # Count and filter source files (applying exclusions)
    console.print("[dim]Counting source files...[/dim]")
    source_files = []
    excluded_count = 0
    total_size = 0

    for path in source.rglob("*"):
        if path.is_file():
            rel_path = path.relative_to(source)
            if _should_exclude(rel_path, config.excludes):
                excluded_count += 1
                continue
            source_files.append(path)
            total_size += path.stat().st_size

    console.print(f"  Files: {len(source_files):,}")
    console.print(f"  Excluded: {excluded_count:,}")
    console.print(f"  Size: {format_bytes(total_size)}")

    # Phase 4: Hash source and generate MHL
    console.print()
    console.print("[bold blue]Phase 4:[/bold blue] Hashing source files and generating MHL...")
    phase4_start = datetime.now(timezone.utc)

    mhl = MHL(
        creator_info=CreatorInfo.default(),
        tape_info=TapeInfo(name=tape_name),
    )
    mhl.creator_info.start_date = phase4_start

    # Store hashes for catalog phase
    file_hashes: dict[str, str] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        DownloadColumn(),
        TextColumn("•"),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Hashing", total=total_size)
        bytes_hashed = 0

        for path in source_files:
            rel_path_raw = path.relative_to(source)
            rel_path = normalize_path(str(rel_path_raw))
            progress.update(task, description=f"Hashing: {rel_path[:60]}")

            try:
                file_hash = hash_file(path)
                file_size = path.stat().st_size

                # Get mtime from tape file (it's what we'll store in MHL)
                tape_file = dest_dir / rel_path
                if tape_file.exists():
                    mtime = datetime.fromtimestamp(tape_file.stat().st_mtime, tz=timezone.utc)
                else:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

                mhl.add_hash(HashEntry(
                    file=rel_path,
                    size=file_size,
                    xxhash64be=file_hash,
                    last_modification_date=mtime,
                    hash_date=datetime.now(timezone.utc),
                ))

                file_hashes[rel_path] = file_hash
                bytes_hashed += file_size
                progress.update(task, completed=bytes_hashed)
            except OSError as e:
                console.print(f"[yellow]Warning:[/yellow] Could not hash {rel_path}: {e}")

    mhl.creator_info.finish_date = datetime.now(timezone.utc)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mhl_path = config.mhl_dir / f"{tape_name}_{source_name}_{timestamp}.mhl"
    mhl.save(mhl_path)

    phase4_end = datetime.now(timezone.utc)
    phase4_duration = (phase4_end - phase4_start).total_seconds()
    phase4_throughput = (total_size / (1024 * 1024)) / phase4_duration if phase4_duration > 0 else 0

    console.print(f"  MHL: {mhl_path}")

    # Phase 5: Update catalog
    console.print()
    console.print("[bold blue]Phase 5:[/bold blue] Updating catalog...")
    phase5_start = datetime.now(timezone.utc)

    catalog_tape_dir = config.catalog_dir / tape_name / source_name
    catalog_tape_dir.mkdir(parents=True, exist_ok=True)

    for rel_path in file_hashes.keys():
        tape_file = dest_dir / rel_path
        catalog_file = catalog_tape_dir / rel_path

        catalog_file.parent.mkdir(parents=True, exist_ok=True)
        catalog_file.touch()

        # Preserve original timestamp from tape
        try:
            import os
            if tape_file.exists():
                stat = tape_file.stat()
                os.utime(catalog_file, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass

    console.print(f"  Catalog: {catalog_tape_dir}")

    # 5b: Update SQLite catalog database
    try:
        from .catalog_db import CatalogDB

        db = CatalogDB(config=config)
        db.add_tape(name=tape_name)

        # Prepare file records: (path, size, mtime, xxhash)
        db_files = []
        for rel_path, file_hash in file_hashes.items():
            # Get size and mtime from source file (faster than tape)
            source_file = source / rel_path
            try:
                stat = source_file.stat()
                file_size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            except OSError:
                # Fall back to tape file
                tape_file = dest_dir / rel_path
                try:
                    stat = tape_file.stat()
                    file_size = stat.st_size
                    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                except OSError:
                    continue

            db_files.append((rel_path, file_size, mtime, file_hash))

        db.add_files(tape_name, db_files, archived_at=datetime.now(timezone.utc))
        console.print(f"  Database: {len(db_files):,} files added")
    except Exception as e:
        # Don't fail recovery if database update fails
        console.print(f"[yellow]Warning:[/yellow] Could not update catalog database: {e}")

    phase5_end = datetime.now(timezone.utc)
    phase5_duration = (phase5_end - phase5_start).total_seconds()

    # Summary
    console.print()
    console.print("[bold]Recovery Summary[/bold]")

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Files", f"{len(file_hashes):,}")
    table.add_row("Size", format_bytes(total_size))
    table.add_row("Phase 4 (Hash+MHL)", f"{phase4_duration:.1f}s ({phase4_throughput:.1f} MB/s)")
    table.add_row("Phase 5 (Catalog)", f"{phase5_duration:.1f}s")
    table.add_row("MHL", str(mhl_path))
    table.add_row("Catalog", str(catalog_tape_dir))

    console.print(table)
    console.print("[green]✓[/green] Recovery completed successfully")


@main.command()
@click.argument("source_name")
@click.argument("tape_name", required=False)
def finalize(source_name: str, tape_name: Optional[str]):
    """Generate MHL and catalog from tape (slower than recover).

    Use this to complete Phase 4 (MHL) and Phase 5 (Catalog) for a transfer
    that crashed after verification. Hashes files on tape and generates outputs.

    SOURCE_NAME is the directory name on the tape (e.g., 'deathstar2').
    TAPE_NAME is used for MHL/catalog naming (default: same as source_name).

    Example:
        ltfs-tool finalize deathstar2
    """
    from datetime import datetime, timezone
    from rich.progress import (
        Progress, SpinnerColumn, TextColumn, BarColumn,
        TimeElapsedColumn, TransferSpeedColumn, DownloadColumn,
    )
    from .hash import hash_file
    from .mhl import MHL, CreatorInfo, HashEntry, TapeInfo

    config = get_config()
    tape_name = tape_name or source_name

    if not config.is_mounted():
        console.print(f"[red]✗[/red] No tape mounted at {config.mount_point}")
        raise SystemExit(1)

    source_dir = config.mount_point / source_name
    if not source_dir.exists():
        console.print(f"[red]✗[/red] Directory not found on tape: {source_dir}")
        raise SystemExit(1)

    console.print(f"[bold]Finalizing transfer:[/bold] {source_name}")
    console.print(f"  Tape directory: {source_dir}")

    # Count files and size
    console.print("[dim]Counting files...[/dim]")
    files = list(source_dir.rglob("*"))
    files = [f for f in files if f.is_file()]
    total_size = sum(f.stat().st_size for f in files)

    console.print(f"  Files: {len(files):,}")
    console.print(f"  Size: {format_bytes(total_size)}")

    # Phase 4: Hash and generate MHL
    console.print()
    console.print("[bold blue]Phase 4:[/bold blue] Hashing and generating MHL file...")
    phase4_start = datetime.now(timezone.utc)

    mhl = MHL(
        creator_info=CreatorInfo.default(),
        tape_info=TapeInfo(name=tape_name),
    )
    mhl.creator_info.start_date = phase4_start

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        DownloadColumn(),
        TextColumn("•"),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Hashing", total=total_size)
        bytes_hashed = 0

        for path in files:
            rel_path = str(path.relative_to(source_dir))
            progress.update(task, description=f"Hashing: {rel_path[:60]}")

            try:
                file_hash = hash_file(path)
                file_size = path.stat().st_size
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

                mhl.add_hash(HashEntry(
                    file=rel_path,
                    size=file_size,
                    xxhash64be=file_hash,
                    last_modification_date=mtime,
                    hash_date=datetime.now(timezone.utc),
                ))

                bytes_hashed += file_size
                progress.update(task, completed=bytes_hashed)
            except OSError as e:
                console.print(f"[yellow]Warning:[/yellow] Could not hash {rel_path}: {e}")

    mhl.creator_info.finish_date = datetime.now(timezone.utc)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mhl_path = config.mhl_dir / f"{tape_name}_{source_name}_{timestamp}.mhl"
    mhl.save(mhl_path)

    phase4_end = datetime.now(timezone.utc)
    phase4_duration = (phase4_end - phase4_start).total_seconds()

    console.print(f"  MHL: {mhl_path}")

    # Phase 5: Update catalog
    console.print()
    console.print("[bold blue]Phase 5:[/bold blue] Updating catalog...")
    phase5_start = datetime.now(timezone.utc)

    catalog_tape_dir = config.catalog_dir / tape_name / source_name
    catalog_tape_dir.mkdir(parents=True, exist_ok=True)

    for path in files:
        rel_path = path.relative_to(source_dir)
        catalog_file = catalog_tape_dir / rel_path

        catalog_file.parent.mkdir(parents=True, exist_ok=True)
        catalog_file.touch()

        # Preserve original timestamp from tape
        try:
            import os
            stat = path.stat()
            os.utime(catalog_file, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass

    console.print(f"  Catalog: {catalog_tape_dir}")

    # 5b: Update SQLite catalog database
    try:
        from .catalog_db import CatalogDB

        db = CatalogDB(config=config)
        db.add_tape(name=tape_name)

        # Prepare file records from MHL entries
        db_files = []
        for entry in mhl.hashes:
            db_files.append((
                entry.file,
                entry.size,
                entry.last_modification_date,
                entry.xxhash64be,
            ))

        db.add_files(tape_name, db_files, archived_at=datetime.now(timezone.utc))
        console.print(f"  Database: {len(db_files):,} files added")
    except Exception as e:
        # Don't fail finalize if database update fails
        console.print(f"[yellow]Warning:[/yellow] Could not update catalog database: {e}")

    phase5_end = datetime.now(timezone.utc)
    phase5_duration = (phase5_end - phase5_start).total_seconds()

    # Summary
    console.print()
    console.print("[bold]Finalize Summary[/bold]")

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Files", f"{len(files):,}")
    table.add_row("Size", format_bytes(total_size))
    table.add_row("Phase 4 (Hash+MHL)", f"{phase4_duration:.1f}s")
    table.add_row("Phase 5 (Catalog)", f"{phase5_duration:.1f}s")
    table.add_row("MHL", str(mhl_path))
    table.add_row("Catalog", str(catalog_tape_dir))

    console.print(table)
    console.print("[green]✓[/green] Finalize completed successfully")


@main.command()
@click.argument("mhl_file", type=click.Path(exists=True, path_type=Path))
@click.argument("base_path", required=False, type=click.Path(exists=True, path_type=Path))
def verify(mhl_file: Path, base_path: Optional[Path]):
    """Verify files against an MHL file.

    MHL_FILE is the Media Hash List to verify against.
    BASE_PATH is the directory containing the files (default: mount point).
    """
    config = get_config()

    try:
        result = verify_func(
            mhl_path=mhl_file,
            base_path=base_path,
            config=config,
        )

        # Print summary
        console.print()
        console.print("[bold]Verification Summary[/bold]")

        table = Table(show_header=False, box=None)
        table.add_column(style="dim")
        table.add_column()

        table.add_row("Total files", str(result.total_files))
        table.add_row("Verified", str(result.verified))
        table.add_row("Failed", str(result.failed))
        table.add_row("Missing", str(result.missing))

        console.print(table)

        if result.success:
            console.print("[green]✓[/green] All files verified successfully")
        else:
            if result.failed_files:
                console.print("[red]Hash mismatches:[/red]")
                for f in result.failed_files[:10]:
                    console.print(f"  - {f}")
                if len(result.failed_files) > 10:
                    console.print(f"  ... and {len(result.failed_files) - 10} more")

            if result.missing_files:
                console.print("[yellow]Missing files:[/yellow]")
                for f in result.missing_files[:10]:
                    console.print(f"  - {f}")
                if len(result.missing_files) > 10:
                    console.print(f"  ... and {len(result.missing_files) - 10} more")

            raise SystemExit(1)

    except VerifyError as e:
        console.print(f"[red]✗[/red] Verification failed: {e}")
        raise SystemExit(1)


@main.command()
@click.option(
    "-m", "--mount-point",
    type=click.Path(path_type=Path),
    help="Mount location",
)
def info(mount_point: Optional[Path]):
    """Display information about a mounted tape."""
    config = get_config()
    mount_point = mount_point or config.mount_point

    # Check if the specified mount point is actually mounted
    def is_mount_point(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            mount_stat = path.stat()
            parent_stat = path.parent.stat()
            return mount_stat.st_dev != parent_stat.st_dev
        except OSError:
            return False

    if not is_mount_point(mount_point):
        console.print(f"[yellow]No tape mounted at {mount_point}[/yellow]")
        raise SystemExit(1)

    tape_info = get_tape_info(mount_point, config, deep_scan=False)

    console.print("[bold]LTFS Tape Information[/bold]")
    console.print()

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Mount Point", str(mount_point))
    table.add_row("Platform", config.platform.name)
    table.add_row("Device", config.device)

    # Tape-specific attributes
    if tape_info.get("volumeName"):
        table.add_row("Volume Name", tape_info["volumeName"])
    if tape_info.get("barcode"):
        table.add_row("Barcode", tape_info["barcode"])
    if tape_info.get("volumeUUID"):
        table.add_row("Volume UUID", tape_info["volumeUUID"])
    if tape_info.get("generation"):
        table.add_row("Generation", str(tape_info["generation"]))
    if tape_info.get("softwareVendor"):
        table.add_row("Software Vendor", tape_info["softwareVendor"])
    if tape_info.get("softwareProduct"):
        table.add_row("Software Product", tape_info["softwareProduct"])
    if tape_info.get("softwareVersion"):
        table.add_row("Software Version", tape_info["softwareVersion"])
    if tape_info.get("softwareFormatSpec"):
        table.add_row("Format Spec", tape_info["softwareFormatSpec"])

    if tape_info.get("total_size"):
        table.add_row("Total Size", format_bytes(tape_info["total_size"]))

    console.print(table)

    # Show top-level contents
    if "top_level_dirs" in tape_info:
        console.print()
        console.print("[bold]Top-level Contents:[/bold]")

        dirs = tape_info.get("top_level_dirs", [])
        files = tape_info.get("top_level_files", [])

        if dirs:
            console.print("  [cyan]Directories:[/cyan]")
            for d in sorted(dirs):
                console.print(f"    {d}/")

        if files:
            console.print("  [cyan]Files:[/cyan]")
            for f in sorted(files):
                console.print(f"    {f}")


@main.group()
def catalog():
    """Manage tape catalogs."""
    pass


@catalog.command("list")
def catalog_list():
    """List all cataloged tapes."""
    config = get_config()
    tapes = catalog_module.list_tapes(config)

    if not tapes:
        console.print("[yellow]No catalogs found[/yellow]")
        return

    console.print("[bold]Cataloged Tapes[/bold]")
    console.print()

    table = Table()
    table.add_column("Tape")
    table.add_column("Files", justify="right")
    table.add_column("Oldest")
    table.add_column("Newest")

    for tape in tapes:
        stats = catalog_module.get_catalog_stats(tape, config)
        table.add_row(
            tape,
            str(stats.get("file_count", 0)),
            stats["oldest_file"].strftime("%Y-%m-%d") if stats.get("oldest_file") else "-",
            stats["newest_file"].strftime("%Y-%m-%d") if stats.get("newest_file") else "-",
        )

    console.print(table)


@catalog.command("search")
@click.argument("pattern")
@click.option("-t", "--tape", help="Limit search to specific tape")
def catalog_search(pattern: str, tape: Optional[str]):
    """Search for files across catalogs.

    PATTERN supports * wildcards.
    """
    config = get_config()
    results = catalog_module.search_catalogs(pattern, tape, config)

    if not results:
        console.print(f"[yellow]No files matching '{pattern}'[/yellow]")
        return

    console.print(f"[bold]Files matching '{pattern}'[/bold]")
    console.print()

    for tape_name, path in results:
        console.print(f"  [{tape_name}] {path}")

    console.print()
    console.print(f"Found {len(results)} file(s)")


@catalog.command("mount")
@click.argument("mount_point", type=click.Path(path_type=Path))
@click.option("-f", "--foreground", is_flag=True, help="Run in foreground (for debugging)")
@click.option("--allow-other", is_flag=True, help="Allow other users to access the mount")
@click.option("--db", is_flag=True, help="Use SQLite database instead of XML indexes (faster)")
def catalog_mount(mount_point: Path, foreground: bool, allow_other: bool, db: bool):
    """Mount catalogs as a virtual filesystem (FUSE).

    Shows real file sizes from LTFS indexes without consuming disk space.
    Inspired by Canister's catalog browsing feature on macOS.

    Use --db for faster mounting when you have a populated catalog database.

    Examples:
        ltfs-tool catalog mount /mnt/catalogs
        ltfs-tool catalog mount /mnt/catalogs --db
        ls -la /mnt/catalogs/TAPE_NAME/
    """
    try:
        from .catalogfs import mount_catalogfs, FUSE_AVAILABLE
    except ImportError:
        FUSE_AVAILABLE = False

    if not FUSE_AVAILABLE:
        console.print("[red]✗[/red] fusepy is not installed.")
        console.print("Install with: [cyan]pip install fusepy[/cyan]")
        console.print("Also ensure FUSE is installed: [cyan]sudo apt install fuse3[/cyan]")
        raise SystemExit(1)

    config = get_config()

    console.print(f"Mounting catalog filesystem at [cyan]{mount_point}[/cyan]...")
    if db:
        console.print(f"  Source: SQLite database ({config.archive_base / 'catalog.db'})")
    else:
        console.print(f"  Index directory: {config.index_dir}")
        console.print(f"  Catalog directory: {config.catalog_dir}")

    if foreground:
        console.print("[dim]Running in foreground. Press Ctrl+C to unmount.[/dim]")
    else:
        console.print("[dim]Running in background. Use 'ltfs-tool catalog unmount' to stop.[/dim]")

    try:
        from .catalogfs import mount_catalogfs
        mount_catalogfs(
            mount_point=mount_point,
            index_dir=config.index_dir,
            catalog_dir=config.catalog_dir,
            foreground=foreground,
            allow_other=allow_other,
            use_database=db,
        )
        console.print("[green]✓[/green] Catalog filesystem mounted")
    except Exception as e:
        console.print(f"[red]✗[/red] Mount failed: {e}")
        raise SystemExit(1)


@catalog.command("unmount")
@click.argument("mount_point", type=click.Path(path_type=Path))
def catalog_unmount(mount_point: Path):
    """Unmount the catalog filesystem.

    Example:
        ltfs-tool catalog unmount /mnt/catalogs
    """
    try:
        from .catalogfs import unmount_catalogfs
    except ImportError:
        console.print("[red]✗[/red] fusepy is not installed.")
        raise SystemExit(1)

    console.print(f"Unmounting catalog filesystem at [cyan]{mount_point}[/cyan]...")

    if unmount_catalogfs(mount_point):
        console.print("[green]✓[/green] Catalog filesystem unmounted")
    else:
        console.print("[red]✗[/red] Unmount failed. Try: [cyan]fusermount -u {mount_point}[/cyan]")
        raise SystemExit(1)


# --- Database-backed catalog commands ---


@catalog.command("db-init")
@click.option("--import-mhls", is_flag=True, help="Import all existing MHL files")
def catalog_db_init(import_mhls: bool):
    """Initialize the SQLite catalog database.

    Creates the database if it doesn't exist and optionally imports
    all existing MHL files.

    Example:
        ltfs-tool catalog db-init --import-mhls
    """
    from .catalog_db import CatalogDB

    config = get_config()
    db = CatalogDB(config=config)

    console.print(f"[green]✓[/green] Database initialized at {db.db_path}")

    if import_mhls:
        console.print()
        console.print("[bold]Importing MHL files...[/bold]")

        mhl_files = list(config.mhl_dir.glob("*.mhl"))
        if not mhl_files:
            console.print("[yellow]No MHL files found[/yellow]")
            return

        total_files = 0
        for mhl_path in mhl_files:
            try:
                count = db.import_from_mhl(mhl_path)
                console.print(f"  {mhl_path.name}: {count} files")
                total_files += count
            except Exception as e:
                console.print(f"  [red]✗[/red] {mhl_path.name}: {e}")

        console.print()
        console.print(f"[green]✓[/green] Imported {total_files} files from {len(mhl_files)} MHL files")


@catalog.command("db-search")
@click.argument("pattern")
@click.option("-t", "--tape", help="Limit search to specific tape")
@click.option("--fts", is_flag=True, help="Use full-text search")
@click.option("-l", "--limit", default=100, help="Maximum results (default: 100)")
@click.option("--summary", is_flag=True, help="Show summary instead of file list")
def catalog_db_search(pattern: str, tape: Optional[str], fts: bool, limit: int, summary: bool):
    """Search for files in the catalog database.

    PATTERN supports * and ? wildcards (or FTS queries with --fts).

    Examples:
        ltfs-tool catalog db-search "*.mov"
        ltfs-tool catalog db-search "project*" --tape TEST01
        ltfs-tool catalog db-search "*.mov" --summary
        ltfs-tool catalog db-search "project AND 2024" --fts
    """
    from .catalog_db import CatalogDB

    config = get_config()
    db = CatalogDB(config=config)

    if fts:
        results = db.search_fts(pattern, tape, limit=limit)
    else:
        results = db.search(pattern, tape, limit=limit)

    if not results:
        console.print(f"[yellow]No files matching '{pattern}'[/yellow]")
        return

    if summary:
        # Group by tape and show totals
        tape_stats: dict[str, tuple[int, int]] = {}
        for r in results:
            if r.tape_name not in tape_stats:
                tape_stats[r.tape_name] = (0, 0)
            count, size = tape_stats[r.tape_name]
            tape_stats[r.tape_name] = (count + 1, size + r.size)

        console.print(f"[bold]Files matching '{pattern}'[/bold]")
        console.print()

        table = Table()
        table.add_column("Tape")
        table.add_column("Files", justify="right")
        table.add_column("Size", justify="right")

        total_files = 0
        total_size = 0
        for tape_name, (count, size) in sorted(tape_stats.items()):
            table.add_row(tape_name, str(count), format_bytes(size))
            total_files += count
            total_size += size

        console.print(table)
        console.print()
        console.print(f"[bold]Total:[/bold] {total_files} files, {format_bytes(total_size)} across {len(tape_stats)} tape(s)")
    else:
        console.print(f"[bold]Files matching '{pattern}'[/bold]")
        console.print()

        table = Table()
        table.add_column("Tape")
        table.add_column("Size", justify="right")
        table.add_column("Path")

        for r in results:
            table.add_row(r.tape_name, format_bytes(r.size), r.path)

        console.print(table)
        console.print()
        console.print(f"Found {len(results)} file(s)" + (f" (limit: {limit})" if len(results) == limit else ""))


@catalog.command("db-stats")
@click.argument("tape_name", required=False)
def catalog_db_stats(tape_name: Optional[str]):
    """Show catalog database statistics.

    If TAPE_NAME is provided, shows stats for that tape only.
    Otherwise shows summary of all tapes.

    Examples:
        ltfs-tool catalog db-stats
        ltfs-tool catalog db-stats TEST01
    """
    from .catalog_db import CatalogDB

    config = get_config()
    db = CatalogDB(config=config)

    if tape_name:
        stats = db.get_tape_stats(tape_name)
        if not stats:
            console.print(f"[yellow]Tape '{tape_name}' not found in database[/yellow]")
            return

        console.print(f"[bold]Tape: {tape_name}[/bold]")
        console.print()

        table = Table(show_header=False, box=None)
        table.add_column(style="dim")
        table.add_column()

        table.add_row("Files", f"{stats.file_count:,}")
        table.add_row("Size", format_bytes(stats.total_bytes))
        table.add_row("Oldest", stats.oldest_file.strftime("%Y-%m-%d %H:%M") if stats.oldest_file else "-")
        table.add_row("Newest", stats.newest_file.strftime("%Y-%m-%d %H:%M") if stats.newest_file else "-")

        console.print(table)
    else:
        tapes = db.list_tapes()
        summary = db.get_summary()

        console.print("[bold]Catalog Database Summary[/bold]")
        console.print()
        console.print(f"  Database: {db.db_path}")
        console.print(f"  Tapes: {summary['tape_count']}")
        console.print(f"  Files: {summary['file_count']:,}")
        console.print(f"  Size: {format_bytes(summary['total_bytes'])}")

        if tapes:
            console.print()
            console.print("[bold]Tapes[/bold]")

            table = Table()
            table.add_column("Name")
            table.add_column("Files", justify="right")
            table.add_column("Size", justify="right")
            table.add_column("UUID")

            for t in tapes:
                table.add_row(
                    t.name,
                    f"{t.file_count:,}",
                    format_bytes(t.total_bytes),
                    t.volume_uuid[:8] if t.volume_uuid else "-",
                )

            console.print(table)


@catalog.command("db-find-hash")
@click.argument("xxhash")
def catalog_db_find_hash(xxhash: str):
    """Find files by XXHash64.

    Useful for checking if a file exists in the archive or finding duplicates.

    Example:
        ltfs-tool catalog db-find-hash abc123def456789
    """
    from .catalog_db import CatalogDB

    config = get_config()
    db = CatalogDB(config=config)

    results = db.find_by_hash(xxhash)

    if not results:
        console.print(f"[yellow]No files with hash '{xxhash}'[/yellow]")
        return

    console.print(f"[bold]Files with hash {xxhash}[/bold]")
    console.print()

    table = Table()
    table.add_column("Tape")
    table.add_column("Size", justify="right")
    table.add_column("Path")

    for r in results:
        table.add_row(r.tape_name, format_bytes(r.size), r.path)

    console.print(table)
    console.print()
    console.print(f"Found {len(results)} file(s)")


@catalog.command("db-duplicates")
@click.option("--min-size", default=1048576, help="Minimum file size in bytes (default: 1MB)")
@click.option("-l", "--limit", default=50, help="Maximum duplicate sets to show (default: 50)")
def catalog_db_duplicates(min_size: int, limit: int):
    """Find duplicate files across all tapes.

    Shows files that exist on multiple tapes (same XXHash64).

    Example:
        ltfs-tool catalog db-duplicates --min-size 10485760
    """
    from .catalog_db import CatalogDB

    config = get_config()
    db = CatalogDB(config=config)

    console.print(f"[bold]Finding duplicates (min size: {format_bytes(min_size)})[/bold]")
    console.print()

    count = 0
    total_wasted = 0

    for xxhash, files in db.find_duplicates(min_size=min_size):
        if count >= limit:
            console.print(f"[dim]... (showing first {limit} duplicate sets)[/dim]")
            break

        # Calculate wasted space (all copies except one)
        file_size = files[0].size
        wasted = file_size * (len(files) - 1)
        total_wasted += wasted

        console.print(f"[cyan]{xxhash}[/cyan] ({format_bytes(file_size)}, {len(files)} copies)")
        for f in files:
            console.print(f"  [{f.tape_name}] {f.path}")
        console.print()

        count += 1

    if count == 0:
        console.print("[green]No duplicates found[/green]")
    else:
        console.print(f"[bold]Found {count} duplicate set(s)[/bold]")
        console.print(f"[bold]Potential space savings: {format_bytes(total_wasted)}[/bold]")


@catalog.command("db-import")
@click.argument("mhl_file", type=click.Path(exists=True, path_type=Path))
@click.option("-t", "--tape", help="Override tape name")
def catalog_db_import(mhl_file: Path, tape: Optional[str]):
    """Import an MHL file into the catalog database.

    Example:
        ltfs-tool catalog db-import /path/to/archive.mhl
        ltfs-tool catalog db-import archive.mhl --tape BACKUP01
    """
    from .catalog_db import CatalogDB

    config = get_config()
    db = CatalogDB(config=config)

    try:
        count = db.import_from_mhl(mhl_file, tape_name=tape)
        console.print(f"[green]✓[/green] Imported {count} files from {mhl_file.name}")
    except Exception as e:
        console.print(f"[red]✗[/red] Import failed: {e}")
        raise SystemExit(1)


# Expose individual commands at module level for entry points
__all__ = ["main", "mount", "unmount", "transfer", "recover", "finalize", "verify", "info", "catalog"]
