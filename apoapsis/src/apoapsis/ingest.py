"""Adopt a repocast run as a post.

repocast produces an article, a narration script, a rendered video and some
publish metadata into its output directory. This module finds those artifacts
and turns them into a Hugo page bundle, preserving whatever front matter
repocast already wrote and layering the taxonomy this site needs on top.

Nothing here assumes exact filenames — repocast's layout is discovered by
glob, and every pattern is overridable in apoapsis.toml under
[repocast]. That keeps the two projects loosely coupled: repocast can
reorganise its output without breaking publication.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from .post import FRONTMATTER_RE, Post, slugify

DEFAULT_PATTERNS = {
    "article": ["article*.md", "*.article.md", "*.md"],
    "video": ["*.mp4", "video/*.mp4"],
    "short": ["*short*.mp4", "short/*.mp4"],
    "thumbnail": ["thumbnail*.png", "cover*.png", "*.jpg"],
    "metadata": ["publish*.json", "metadata*.json", "*.json"],
}

# repocast writes its own front matter; these keys are ours to control.
OURS = {
    "categories", "series", "series_order", "subjects", "tags",
    "devto", "hashnode", "medium", "youtube", "canonical", "links",
}


class IngestError(RuntimeError):
    pass


def _first_match(root: Path, patterns: list[str], exclude: set[Path] | None = None) -> Path | None:
    exclude = exclude or set()
    for pattern in patterns:
        for candidate in sorted(root.glob(pattern)):
            if candidate.is_file() and candidate not in exclude:
                return candidate
    return None


def discover_artifacts(run_dir: Path, overrides: dict | None = None) -> dict[str, Path]:
    """Locate what repocast produced. Missing pieces are simply absent."""
    if not run_dir.is_dir():
        raise IngestError(f"{run_dir} is not a directory")

    patterns = {**DEFAULT_PATTERNS, **(overrides or {})}
    found: dict[str, Path] = {}

    # Resolve the short first so the main video glob does not claim it.
    short = _first_match(run_dir, _as_list(patterns["short"]))
    if short:
        found["short"] = short

    video = _first_match(run_dir, _as_list(patterns["video"]), exclude={short} if short else None)
    if video:
        found["video"] = video

    for key in ("article", "thumbnail", "metadata"):
        match = _first_match(run_dir, _as_list(patterns[key]))
        if match:
            found[key] = match

    if "article" not in found:
        raise IngestError(
            f"No article markdown found under {run_dir}. "
            "Run `repocast article <repo>` first, or set [repocast] article "
            "in apoapsis.toml."
        )
    return found


def _as_list(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def split_article(path: Path) -> tuple[dict, str]:
    """Return (front matter, body). repocast may or may not have written any."""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), match.group(2)


def _title_from(meta: dict, body: str, fallback: str) -> str:
    if meta.get("title"):
        return str(meta["title"])
    heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if heading:
        return heading.group(1).strip()
    return fallback.replace("-", " ").replace("_", " ").title()


def ingest(
    run_dir: Path,
    content_dir: Path,
    *,
    slug: str | None = None,
    category: str = "code-review",
    series: str = "",
    series_order: int | None = None,
    subjects: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    repo: str = "",
    patterns: dict | None = None,
    copy_video: bool = False,
    force: bool = False,
) -> Post:
    """Build a page bundle from a repocast run and return the new Post."""
    artifacts = discover_artifacts(run_dir, patterns)
    meta, body = split_article(artifacts["article"])

    metadata: dict = {}
    if "metadata" in artifacts:
        try:
            metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}

    title = _title_from(meta, body, run_dir.name)
    slug = slug or slugify(title)
    bundle = content_dir / slug

    if bundle.exists() and not force:
        raise IngestError(f"{bundle} already exists. Pass --force to overwrite.")
    bundle.mkdir(parents=True, exist_ok=True)

    # Strip a leading H1: Hugo renders the title from front matter, and a
    # duplicate heading looks wrong on every platform we syndicate to.
    body = re.sub(r"\A\s*#\s+.+?\n+", "", body)

    front = {k: v for k, v in meta.items() if k not in OURS}
    front.setdefault("title", title)
    front.setdefault("date", metadata.get("date") or _now())
    front["draft"] = True
    front.setdefault(
        "summary",
        str(meta.get("summary") or meta.get("description") or metadata.get("summary") or "")[:280],
    )
    front["categories"] = [category]
    front["series"] = [series] if series else []
    if series:
        front["series_order"] = series_order or 1
    front["subjects"] = list(subjects) or _as_list(meta.get("subjects") or [])
    front["tags"] = [str(t).lower() for t in (tags or meta.get("tags") or [])][:4]
    front["repo"] = repo or meta.get("repo") or metadata.get("repo") or ""
    front["canonical"] = ""
    front["devto"] = True
    front["hashnode"] = False
    front["medium"] = True
    front["youtube"] = "video" in artifacts

    # Point at the rendered video where it already lives. Copying a 200 MB mp4
    # into the content tree would bloat the git repo and GitHub Pages does not
    # need it — YouTube hosts the video, this site just embeds it.
    if "video" in artifacts:
        source = artifacts["video"]
        if copy_video:
            shutil.copy2(source, bundle / source.name)
            front["video_file"] = str((bundle / source.name).resolve())
        else:
            front["video_file"] = str(source.resolve())
    if "short" in artifacts:
        front["short_file"] = str(artifacts["short"].resolve())

    # Thumbnails are small and belong with the post as a page-bundle resource.
    if "thumbnail" in artifacts:
        thumb = artifacts["thumbnail"]
        shutil.copy2(thumb, bundle / thumb.name)
        front["cover"] = {"image": thumb.name, "alt": title, "relative": True}

    front["repocast_run"] = str(run_dir.resolve())

    parts = ["{{< series >}}" if series else "", "{{< video >}}", "", body.strip(), ""]
    (bundle / "index.md").write_text(
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip()
        + "\n---\n\n"
        + "\n".join(p for p in parts if p is not None),
        encoding="utf-8",
    )

    return Post.load(bundle / "index.md")


def _now() -> str:
    import datetime as dt

    return dt.datetime.now().astimezone().isoformat(timespec="seconds")
