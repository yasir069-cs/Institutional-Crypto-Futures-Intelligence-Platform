"""Clean up unused imports flagged by pyflakes.

This script reads pyflakes output and removes unused imports safely.
It only removes imports that pyflakes explicitly flagged as unused.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Run pyflakes and capture unused import warnings
result = subprocess.run(
    [sys.executable, "-m", "pyflakes", str(ROOT / "app")],
    capture_output=True, text=True
)

# Parse: "file:line:col: 'module.name' imported but unused"
unused_pattern = re.compile(
    r"^(.+?):(\d+):(\d+): '([^']+)' imported but unused$"
)

# Group by file
files_to_fix: dict[str, list[tuple[int, str]]] = {}
for line in result.stdout.splitlines():
    m = unused_pattern.match(line)
    if m:
        filepath, lineno, col, module = m.groups()
        files_to_fix.setdefault(filepath, []).append((int(lineno), module))

print(f"Found {sum(len(v) for v in files_to_fix.values())} unused imports in {len(files_to_fix)} files")


def remove_unused_imports(filepath: str, unused: list[tuple[int, str]]) -> int:
    """Remove unused import lines from a file. Returns count removed."""
    path = Path(filepath)
    content = path.read_text()
    lines = content.split("\n")
    removed = 0

    # Sort unused by line descending so we can remove without offset issues
    unused_by_line = {ln: mod for ln, mod in unused}

    new_lines = []
    for i, line in enumerate(lines, 1):
        if i in unused_by_line:
            mod = unused_by_line[i]
            # Check if this line is a single import (most common case)
            stripped = line.strip()
            # Pattern: "from X import Y" or "import X"
            if stripped.startswith("from ") and " import " in stripped:
                # Single name import: "from X import Y"
                if stripped.endswith(f" import {mod.split('.')[-1]}"):
                    # Skip this line
                    removed += 1
                    continue
                # Multi-name import: "from X import Y, Z" — leave alone for safety
                new_lines.append(line)
            elif stripped.startswith("import "):
                if stripped == f"import {mod}" or stripped == f"import {mod.split('.')[0]}":
                    removed += 1
                    continue
                new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if removed > 0:
        path.write_text("\n".join(new_lines))
    return removed


total_removed = 0
for filepath, unused in files_to_fix.items():
    full_path = filepath
    if not Path(full_path).is_absolute():
        full_path = str(ROOT / full_path)
    removed = remove_unused_imports(full_path, unused)
    if removed:
        print(f"  {filepath}: removed {removed} unused imports")
        total_removed += removed

print(f"\nTotal unused imports removed: {total_removed}")
