"""Reading, validating and rewriting Hugo posts."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import yaml

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)

# Front-matter keys apoapsis understands. Anything else passes through
# untouched so Hugo-specific params are never clobbered on rewrite.
REQUIRED_FIELDS = ("title", "date", "summary", "categories")


class PostError(ValueError):
    pass


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text) or "untitled"


@dataclass
class Post:
    path: Path
    meta: dict
    body: str

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Post":
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        if not match:
            raise PostError(f"{path}: no YAML front matter delimited by ---")
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise PostError(f"{path}: front matter is not valid YAML — {exc}") from exc
        if not isinstance(meta, dict):
            raise PostError(f"{path}: front matter must be a mapping")
        return cls(path=path, meta=meta, body=match.group(2))

    # ---- derived properties ---------------------------------------------

    @property
    def slug(self) -> str:
        if self.meta.get("slug"):
            return str(self.meta["slug"])
        # Page bundles: content/posts/my-post/index.md -> "my-post"
        if self.path.stem == "index":
            return self.path.parent.name
        return self.path.stem

    @property
    def title(self) -> str:
        return str(self.meta.get("title", "")).strip()

    @property
    def is_draft(self) -> bool:
        return bool(self.meta.get("draft", False))

    @property
    def series(self) -> list[str]:
        return _as_list(self.meta.get("series"))

    @property
    def categories(self) -> list[str]:
        return _as_list(self.meta.get("categories"))

    @property
    def subjects(self) -> list[str]:
        return _as_list(self.meta.get("subjects"))

    @property
    def tags(self) -> list[str]:
        return _as_list(self.meta.get("tags"))

    def url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/posts/{self.slug}/"

    def wants(self, target: str) -> bool:
        return bool(self.meta.get(target, False))

    # ---- content for syndication ----------------------------------------

    def syndication_markdown(self, base_url: str) -> str:
        """Body prepared for a third-party platform.

        Hugo shortcodes are stripped (they mean nothing off-site) and relative
        image/link paths are rewritten to absolute URLs.
        """
        text = re.sub(r"\{\{[<%].*?[>%]\}\}", "", self.body)

        post_url = self.url(base_url)

        def absolutize(match: re.Match) -> str:
            prefix, target = match.group(1), match.group(2)
            if target.startswith(("http://", "https://", "#", "mailto:", "data:")):
                return match.group(0)
            if target.startswith("/"):
                return f"{prefix}({base_url.rstrip('/')}{target})"
            # Resolve ./ and ../ against the post's own URL so page-bundle
            # assets survive the trip to another platform.
            resolved = urljoin(post_url, target)
            return f"{prefix}({resolved})"

        text = re.sub(r"(!?\[[^\]]*\])\(([^)\s]+)\)", absolutize, text)
        return text.strip()

    # ---- validation ------------------------------------------------------

    def validate(self) -> list[str]:
        problems: list[str] = []

        for field_name in REQUIRED_FIELDS:
            value = self.meta.get(field_name)
            if value in (None, "", [], {}):
                problems.append(f"missing or empty '{field_name}'")

        date = self.meta.get("date")
        if date is not None and not isinstance(date, (dt.date, dt.datetime, str)):
            problems.append("'date' must be a date or an ISO-8601 string")

        summary = str(self.meta.get("summary") or "")
        if summary and len(summary) > 300:
            problems.append(f"'summary' is {len(summary)} chars; keep it under 300")

        if self.series and not isinstance(self.meta.get("series_order"), int):
            problems.append("post is in a series but 'series_order' is not an integer")

        for name in ("categories", "series", "subjects", "tags"):
            value = self.meta.get(name)
            if value is not None and not isinstance(value, (list, str)):
                problems.append(f"'{name}' must be a string or a list of strings")

        if self.wants("devto") and len(self.tags) > 4:
            problems.append(
                f"dev.to accepts at most 4 tags; this post has {len(self.tags)}"
            )

        for tag in self.tags:
            if not re.fullmatch(r"[a-z0-9]+", str(tag)):
                problems.append(
                    f"tag '{tag}' is not portable — dev.to allows lowercase "
                    "alphanumerics only"
                )

        return problems

    # ---- mutation --------------------------------------------------------

    def set(self, key: str, value) -> None:
        self.meta[key] = value

    def save(self) -> None:
        front = yaml.safe_dump(
            self.meta,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).rstrip()
        self.path.write_text(
            f"---\n{front}\n---\n\n{self.body.lstrip()}",
            encoding="utf-8",
        )


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if str(v).strip()]


def discover(content_dir: Path) -> list[Post]:
    """All posts under content_dir, page bundles and flat files alike."""
    paths = sorted(
        {*content_dir.rglob("*.md")} - {*content_dir.rglob("_index.md")}
    )
    posts = []
    for path in paths:
        posts.append(Post.load(path))
    return posts


def find(content_dir: Path, slug: str) -> Post:
    for post in discover(content_dir):
        if post.slug == slug:
            return post
    raise PostError(f"No post with slug '{slug}' under {content_dir}")
