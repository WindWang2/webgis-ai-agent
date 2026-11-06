"""Race-safe broker file opens relative to trusted directory descriptors."""
from __future__ import annotations
import os
import stat
from pathlib import Path


def open_confined_fd(root: Path, target: Path, flags: int, *, create_parents: bool = False) -> int:
    relative = target.relative_to(root)
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("artifact target must name a confined file")
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("secure directory-relative artifact I/O is unavailable on this platform")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            if create_parents:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    if not stat.S_ISDIR(os.stat(part, dir_fd=directory, follow_symlinks=False).st_mode):
                        raise OSError("artifact ancestor is not a directory")
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(relative.name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    finally:
        os.close(directory)
