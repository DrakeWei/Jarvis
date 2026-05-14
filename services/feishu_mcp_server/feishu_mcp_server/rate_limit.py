from __future__ import annotations

import threading


class DocumentWriteCoordinator:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock_for(self, document_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(document_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[document_id] = lock
            return lock


write_coordinator = DocumentWriteCoordinator()
