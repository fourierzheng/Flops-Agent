import shutil
from pathlib import Path

from flops.logger import logger


class Snapshot:
    """Manages file backups for undo.

    Each backup is idempotent per resolved path within a session.
    """

    def __init__(self, trash_dir: Path, sessions_dir: Path):
        self._snapshots: list[Path] = []
        self._trash_dir = trash_dir
        self._trash_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_orphans(sessions_dir)

    def _cleanup_orphans(self, sessions_dir: Path):
        """Remove trash dirs for sessions that no longer have session files."""
        parent = self._trash_dir.parent
        if not parent.exists():
            return
        for d in list(parent.iterdir()):
            if not d.is_dir() or d == self._trash_dir:
                continue
            if not (sessions_dir / d.name).exists():
                try:
                    shutil.rmtree(d)
                    logger.debug(f"Cleaned up trash for deleted session: {d.name}")
                except OSError:
                    pass

    def backup(self, file_path: str | Path) -> None:
        """Backup a file or directory to trash for undo. Idempotent per resolved path."""
        resolved = Path(file_path).resolve()
        if resolved in self._snapshots:
            return
        if not resolved.exists():
            return
        dst = self._trash_dir / resolved.relative_to("/")
        if resolved.is_dir():
            shutil.copytree(resolved, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, dst)
        self._snapshots.append(resolved)
        logger.debug(f"Snapshot: backed up {resolved}")

    def restore_all(self) -> int:
        """Restore all backed-up files. Returns count of restored files."""
        restored = 0
        for fp in self._snapshots:
            backup = self._trash_dir / fp.relative_to("/")
            if backup.exists():
                if backup.is_dir():
                    shutil.copytree(backup, fp, dirs_exist_ok=True)
                else:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, fp)
                restored += 1
            else:
                # No backup → file was created by AI → delete it
                if fp.exists():
                    shutil.rmtree(fp) if fp.is_dir() else fp.unlink()
                    restored += 1
        return restored

    def clear(self) -> None:
        """Clear snapshot records and trash directory."""
        self._snapshots.clear()
        if self._trash_dir.exists():
            shutil.rmtree(self._trash_dir)
        self._trash_dir.mkdir(parents=True, exist_ok=True)
