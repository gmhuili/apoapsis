"""Common interface for every syndication target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import Config, TargetConfig
from ..post import Post
from ..state import State


@dataclass
class Result:
    target: str
    action: str          # "created" | "updated" | "skipped" | "manual"
    url: str = ""
    detail: str = ""

    def __str__(self) -> str:
        line = f"{self.target:<10} {self.action:<8}"
        if self.url:
            line += f" {self.url}"
        if self.detail:
            line += f"  ({self.detail})"
        return line


class Target(Protocol):
    name: str

    def push(
        self,
        post: Post,
        config: Config,
        target_config: TargetConfig,
        state: State,
        *,
        dry_run: bool = False,
    ) -> Result: ...


class SyndicationError(RuntimeError):
    pass
