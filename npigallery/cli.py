import argparse
import sys
from pathlib import Path

from .formatting import format_timestamp, human_size
from .npi import (
    NpiError,
    inspect_image,
    is_npi_path,
    read_exif_tags,
    read_image_bytes,
    read_record,
    verify_npi,
)
from .services.batch import BatchResult, dump_folder, restore_folder
from .services import shell_registration
from .services.scanner import iter_media_paths


def _print_progress(done: int, total: int) -> None:
    sys.stderr.write(f"\r{done}/{total}")
    if done == total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def _report(result: BatchResult) -> int:
    print(f"processed: {result.processed}  skipped: {result.skipped}  failed: {len(result.failures)}")
    for path, message in result.failures:
        print(f"FAILED {path}: {message}")
    if result.cancelled:
        print("cancelled")
    return 1 if result.failures else 0


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _handle_dump(args: argparse.Namespace) -> int:
    result = dump_folder(
        Path(args.source),
        _optional_path(args.output),
        not args.no_recursive,
        args.delete_originals,
        _print_progress,
    )
    return _report(result)


def _handle_restore(args: argparse.Namespace) -> int:
    result = restore_folder(
        Path(args.source),
        _optional_path(args.output),
        not args.no_recursive,
        args.delete_npi,
        _print_progress,
    )
    return _report(result)


def _handle_verify(args: argparse.Namespace) -> int:
    failures = 0
    for target in map(Path, args.paths):
        paths = [target] if target.is_file() else list(iter_media_paths(target))
        for path in paths:
            if not is_npi_path(path):
                continue
            try:
                verify_npi(path)
                print(f"OK     {path}")
            except (NpiError, OSError) as error:
                failures += 1
                print(f"FAILED {path}: {error}")
    return 1 if failures else 0


def _handle_info(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if is_npi_path(path):
        meta = read_record(path).meta
        print("container      : npi")
        print(f"original name  : {meta.original_name}")
        print(f"original format: {meta.image_format.name}")
        print(f"original size  : {human_size(meta.size)}")
        print(f"modified       : {format_timestamp(meta.mtime_ns / 1e9)}")
        print(f"sha256         : {meta.sha256}")
    data = read_image_bytes(path)
    info = inspect_image(data)
    print(f"dimensions     : {info.width} x {info.height}")
    for name, value in read_exif_tags(info.exif):
        print(f"exif {name}: {value}")
    return 0


def _handle_register(args: argparse.Namespace) -> int:
    try:
        shell_registration.register(include_npi=args.npi)
    except shell_registration.RegistrationError as error:
        print(f"FAILED {error}")
        return 1
    print("NPI Gallery is now listed in Open with for photos and videos" + (" and NPI files" if args.npi else ""))
    return 0


def _handle_unregister(_args: argparse.Namespace) -> int:
    try:
        shell_registration.unregister()
    except shell_registration.RegistrationError as error:
        print(f"FAILED {error}")
        return 1
    print("NPI Gallery was removed from Open with")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="npigallery")
    commands = parser.add_subparsers(dest="command", required=True)

    dump = commands.add_parser("dump")
    dump.add_argument("source")
    dump.add_argument("-o", "--output")
    dump.add_argument("--no-recursive", action="store_true")
    dump.add_argument("--delete-originals", action="store_true")
    dump.set_defaults(handler=_handle_dump)

    restore = commands.add_parser("restore")
    restore.add_argument("source")
    restore.add_argument("-o", "--output")
    restore.add_argument("--no-recursive", action="store_true")
    restore.add_argument("--delete-npi", action="store_true")
    restore.set_defaults(handler=_handle_restore)

    verify = commands.add_parser("verify")
    verify.add_argument("paths", nargs="+")
    verify.set_defaults(handler=_handle_verify)

    info = commands.add_parser("info")
    info.add_argument("path")
    info.set_defaults(handler=_handle_info)

    register = commands.add_parser("register")
    register.add_argument("--npi", action="store_true", help="also list NPI Gallery for .npi files")
    register.set_defaults(handler=_handle_register)

    unregister = commands.add_parser("unregister")
    unregister.set_defaults(handler=_handle_unregister)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())