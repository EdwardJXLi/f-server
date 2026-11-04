from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from f_server.config import get_settings
from f_server.fdroid.index import write_unsigned_indexes
from f_server.fdroid.sign import sign_indexes
from f_server.storage import get_storage

_lock = threading.Lock()


def rebuild_repo(session: Session) -> list[str]:
    settings = get_settings()
    storage = get_storage()
    with _lock, tempfile.TemporaryDirectory(prefix="f-server-repo-") as tmp:
        repodir = Path(tmp) / "repo"
        files = write_unsigned_indexes(session, settings, repodir, storage=storage)
        files.extend(sign_indexes(repodir, settings))
        uploaded = []
        for path in files:
            key = f"repo/{path.name}"
            with path.open("rb") as fp:
                storage.put(key, fp, "application/java-archive" if path.suffix == ".jar" else "application/json")
            uploaded.append(key)
        diff_dir = repodir / "diff"
        if diff_dir.exists():
            for path in diff_dir.glob("*.json"):
                key = f"repo/diff/{path.name}"
                with path.open("rb") as fp:
                    storage.put(key, fp, "application/json")
                uploaded.append(key)
        return uploaded
