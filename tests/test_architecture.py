import ast
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent / "npigallery" / "gui"

BASE = {"common", "theme"}
ALLOWED = {
    "common": BASE,
    "theme": BASE,
    "gallery": BASE,
    "sidebar": BASE,
    "batch": BASE,
    "viewer": BASE,
    "settings": BASE | {"sidebar"},
    "main_window": BASE | {"gallery", "sidebar", "settings", "batch", "viewer"},
}


def _package_parts(path: Path) -> list[str]:
    parts = list(path.relative_to(GUI.parent.parent).with_suffix("").parts)
    return parts if parts[-1] == "__init__" else parts[:-1]


def _targets(path: Path):
    package = _package_parts(path)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package[: len(package) - (node.level - 1)]
            yield node, base + (node.module.split(".") if node.module else [])
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node, node.module.split(".")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name.split(".")


def _layer(parts: list[str]) -> str | None:
    if len(parts) >= 3 and parts[:2] == ["npigallery", "gui"]:
        return parts[2]
    return None


def test_gui_packages_respect_dependency_rules():
    violations = []
    for path in sorted(GUI.rglob("*.py")):
        relative = path.relative_to(GUI)
        if len(relative.parts) < 2:
            continue
        owner = relative.parts[0]
        for node, target in _targets(path):
            layer = _layer(target)
            if layer is None or layer == owner or layer not in ALLOWED:
                continue
            if layer not in ALLOWED[owner]:
                violations.append(f"{relative}:{node.lineno} imports gui.{layer}")
    assert not violations, "\n".join(violations)


def test_every_gui_directory_is_a_package():
    missing = [
        str(folder.relative_to(GUI))
        for folder in GUI.rglob("*")
        if folder.is_dir() and folder.name != "__pycache__" and not (folder / "__init__.py").exists()
    ]
    assert not missing
