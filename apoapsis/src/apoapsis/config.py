"""Configuration loading for apoapsis.

Secrets are never stored in the config file. The config only names the
environment variables that hold them, so `apoapsis.toml` stays committable.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class SiteConfig:
    base_url: str
    root: Path
    content_dir: Path
    default_author: str = ""


@dataclass(frozen=True)
class TargetConfig:
    enabled: bool = False
    options: dict = field(default_factory=dict)

    def secret(self, key: str) -> str:
        """Resolve a secret by reading the env var named in the config."""
        var = self.options.get(key)
        if not var:
            raise ConfigError(
                f"Config is missing '{key}' — it should name an environment "
                f"variable, e.g. {key} = \"DEVTO_API_KEY\""
            )
        value = os.environ.get(var)
        if not value:
            raise ConfigError(
                f"Environment variable {var} is not set. "
                f"Export it before publishing: export {var}=..."
            )
        return value


@dataclass(frozen=True)
class Config:
    site: SiteConfig
    targets: dict[str, TargetConfig]
    state_file: Path
    git_remote: str = "origin"
    git_branch: str = "main"
    repocast_patterns: dict = field(default_factory=dict)
    kind_overrides: dict = field(default_factory=dict)

    @property
    def kinds(self) -> dict:
        from .kinds import build

        return build(self.kind_overrides)

    def target(self, name: str) -> TargetConfig:
        return self.targets.get(name, TargetConfig())


def find_config(start: Path | None = None) -> Path:
    """Walk upward from `start` looking for apoapsis.toml."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        found = candidate / "apoapsis.toml"
        if found.is_file():
            return found
    raise ConfigError(
        "No apoapsis.toml found in this directory or any parent. "
        "Copy apoapsis.toml.example to apoapsis.toml and edit it."
    )


def load(path: Path | None = None) -> Config:
    path = path or find_config()
    root = path.parent

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    site_raw = raw.get("site", {})
    if "base_url" not in site_raw:
        raise ConfigError("apoapsis.toml needs a [site] section with base_url")

    site_root = (root / site_raw.get("root", "site")).resolve()
    site = SiteConfig(
        base_url=site_raw["base_url"].rstrip("/"),
        root=site_root,
        content_dir=site_root / site_raw.get("content_dir", "content/posts"),
        default_author=site_raw.get("author", ""),
    )

    targets: dict[str, TargetConfig] = {}
    for name, block in raw.get("targets", {}).items():
        if not isinstance(block, dict):
            continue
        options = {k: v for k, v in block.items() if k != "enabled"}
        targets[name] = TargetConfig(
            enabled=bool(block.get("enabled", False)),
            options=options,
        )

    git = raw.get("git", {})

    return Config(
        site=site,
        targets=targets,
        state_file=(root / raw.get("state_file", ".apoapsis-state.json")).resolve(),
        repocast_patterns=raw.get("repocast", {}),
        kind_overrides=raw.get("kinds", {}),
        git_remote=git.get("remote", "origin"),
        git_branch=git.get("branch", "main"),
    )
