"""Write tokens, keys and other secrets without a window where the file is world-readable.

On Linux and macOS the file is created with mode 0600 before any bytes are written. Windows ignores that mode,
so the best we can do there is keep the files inside the data folder and add them to .gitignore. The rename at
the end means a reader never sees a half-written file.
"""

import os
from pathlib import Path


def write_private(path: Path, data: bytes):
    """Atomically replace `path` with `data`, created as private from the first byte."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
    except OSError:  # an unusual filesystem: still write it, just without the early permission check
        tmp.write_bytes(data)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
