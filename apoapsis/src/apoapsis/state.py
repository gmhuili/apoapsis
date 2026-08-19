"""Per-slug record of what has been pushed where.

This is what makes `publish` safe to re-run: if a slug already has a dev.to
article id, the syndicator updates that article instead of creating a duplicate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}
        if path.is_file():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # A corrupt state file should not block publishing; keep the
                # old one aside so nothing is silently lost.
                path.rename(path.with_suffix(".json.corrupt"))
                self._data = {}

    def entry(self, slug: str) -> dict:
        return self._data.setdefault(slug, {})

    def record(self, slug: str, target: str, **fields) -> None:
        record = self.entry(slug).setdefault(target, {})
        record.update(fields)
        record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save()

    def get(self, slug: str, target: str, key: str, default=None):
        return self.entry(slug).get(target, {}).get(key, default)

    def all(self) -> dict:
        return self._data

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
