import sys

COMMANDS = frozenset({"dump", "restore", "verify", "info", "register", "unregister"})


def main() -> int:
    from . import profiling

    profiling.mark("python started")
    arguments = sys.argv[1:]
    if arguments and arguments[0] in COMMANDS:
        from .cli import main as run_cli

        return run_cli(arguments)
    profiling.mark("importing GUI modules (PySide6, Pillow, ...)")
    from .gui.app import run

    profiling.mark("GUI modules imported")
    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())