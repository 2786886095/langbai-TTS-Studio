from __future__ import annotations

import contextlib
import functools
import hashlib
import http.server
import threading
import zipfile
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def local_http_fixture(directory: Path) -> Iterator[str]:
    """Serve tiny test artifacts locally; production URL validation must stay enabled."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def create_tiny_source_archive(path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("fixture-commit/README.md", "fixture repository\n")
        archive.writestr("fixture-commit/LICENSE", "fixture license\n")
    payload = path.read_bytes()
    return {"path": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def write_tiny_model(path: Path, *, size: int = 4096) -> dict[str, object]:
    payload = (b"langbai-installer-fixture\n" * ((size // 27) + 1))[:size]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": path,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
