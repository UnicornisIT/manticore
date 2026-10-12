"""Reject runtime or sensitive artifacts before they enter Git."""

from __future__ import annotations

import sys
from pathlib import PurePosixPath


ALLOWED_PATHS = {
    ".env.example",
    "static/template.xlsx",
    "templates/fallback_changes.html",
}


def normalize(raw_path: str) -> str:
    return PurePosixPath(raw_path.replace("\\", "/")).as_posix().lower()


def is_forbidden(raw_path: str) -> bool:
    path = normalize(raw_path)
    name = PurePosixPath(path).name
    if path in ALLOWED_PATHS:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return (
        name.endswith((".db", ".xlsx", ".xls"))
        or ".sqlite" in name
        or name.startswith("desktop-config.json")
        or name.startswith("fallback")
        or name.startswith("baze_backup_")
    )


def main(paths: list[str]) -> int:
    forbidden = sorted({path for path in paths if is_forbidden(path)})
    if not forbidden:
        return 0
    print("Forbidden runtime or sensitive artifacts:", file=sys.stderr)
    for path in forbidden:
        print(f"- {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
