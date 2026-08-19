"""`apoapsis` (alias `apo`) — scaffold, check, build, deploy and syndicate posts."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import click
import yaml

from . import config as config_mod
from . import ingest as ingest_mod
from . import kinds as kinds_mod
from . import post as post_mod
from . import video as video_mod
from .config import ConfigError
from .post import Post, PostError, slugify
from .state import State
from .targets import REGISTRY, SyndicationError

ORDER = ["devto", "hashnode", "medium", "youtube"]


def _load():
    try:
        return config_mod.load()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


# External binaries apoapsis shells out to, with a hint for each so a missing
# tool produces an instruction rather than a traceback.
TOOL_HINTS = {
    "hugo": (
        "Install Hugo extended 0.146 or newer. The version in Ubuntu's apt is "
        "too old for PaperMod and will fail to build.\n"
        "  wget https://github.com/gohugoio/hugo/releases/download/"
        "v0.152.0/hugo_extended_0.152.0_linux-amd64.deb\n"
        "  sudo dpkg -i hugo_extended_0.152.0_linux-amd64.deb\n"
        "  hugo version   # must report +extended"
    ),
    "npx": (
        "Install Node.js — npx ships with it and is needed to build the "
        "Pagefind search index.\n"
        "  sudo apt install nodejs npm"
    ),
    "git": "Install git:  sudo apt install git",
}


def _require(tool: str) -> None:
    """Fail with an actionable message when a required binary is absent."""
    if shutil.which(tool) is not None:
        return
    hint = TOOL_HINTS.get(tool, "")
    message = f"`{tool}` is not on your PATH."
    if hint:
        message += f"\n\n{hint}"
    raise click.ClickException(message)


def _run(command: list[str], cwd: Path | None = None) -> None:
    _require(command[0])
    try:
        result = subprocess.run(command, cwd=cwd, check=False)
    except FileNotFoundError as exc:  # PATH changed mid-run, or exec bit missing
        raise click.ClickException(f"Could not execute `{command[0]}`: {exc}") from exc
    if result.returncode != 0:
        raise click.ClickException(
            f"`{' '.join(command)}` failed with status {result.returncode}"
        )


@click.group()
@click.version_option("1.0.0", prog_name="apoapsis")
def cli() -> None:
    """Publish technical reviews and essays from one markdown source."""


# --------------------------------------------------------------------------
# authoring
# --------------------------------------------------------------------------


@cli.command()
@click.argument("title")
@click.option("--kind", "-k", default="essay", show_default=True,
              help="Content type. Run `apo kinds` to see them all.")
@click.option("--category", "-c", default=None,
              help="Override the category the kind would use.")
@click.option("--series", "-s", default="", help="Series name this post belongs to.")
@click.option("--order", "-n", type=int, default=None, help="Position within series.")
@click.option("--subject", "-u", multiple=True, help="Fine-grained topic. Repeatable.")
@click.option("--tag", "-t", multiple=True, help="Lowercase alphanumeric. Max 4.")
@click.option("--repo", default="", help="Repository this post examines.")
def new(title, kind, category, series, order, subject, tag, repo):
    """Scaffold a new post as a page bundle."""
    cfg = _load()
    try:
        spec = kinds_mod.resolve(cfg.kinds, kind)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc

    slug = slugify(title)
    bundle = cfg.site.content_dir / slug
    target = bundle / "index.md"
    if target.exists():
        raise click.ClickException(f"{target} already exists")

    if series and order is None:
        existing = [
            p for p in post_mod.discover(cfg.site.content_dir) if series in p.series
        ]
        order = len(existing) + 1

    meta = {
        "title": title,
        "date": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "draft": True,
        "post_kind": spec.name,
        "summary": "",
        "categories": [category or spec.category],
        "series": [series] if series else [],
        "subjects": list(subject),
        "tags": [t.lower() for t in tag] or list(spec.tags),
        "canonical": "",
        "devto": True,
        "hashnode": False,
        "medium": True,
        "youtube": False,
    }
    if series:
        meta["series_order"] = order
    if spec.needs_repo or repo:
        meta["repo"] = repo
    meta.update(spec.extra)

    body_parts = []
    if series:
        body_parts.append("{{< series >}}")
    if spec.video:
        body_parts.append("{{< video >}}")
    if body_parts:
        body_parts.append("")
    for heading in spec.outline:
        body_parts += [f"## {heading}", "", ""]
    if not spec.outline:
        body_parts.append("")

    bundle.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip()
        + "\n---\n\n"
        + "\n".join(body_parts),
        encoding="utf-8",
    )

    click.secho(f"Created {target}", fg="green")
    click.echo(f"  kind: {spec.name}  ({spec.description})")
    if spec.requires:
        click.secho(
            f"  fill in before publishing: {', '.join(spec.requires)}", fg="yellow"
        )


@cli.command()
def kinds():
    """List the content types this site publishes."""
    cfg = _load()
    for name, spec in sorted(cfg.kinds.items()):
        click.secho(f"{name}", bold=True, nl=False)
        click.echo(f"  →  category: {spec.category}")
        if spec.description:
            click.echo(f"    {spec.description}")
        if spec.outline:
            click.echo(f"    sections: {' · '.join(spec.outline)}")
        if spec.requires:
            click.echo(f"    requires: {', '.join(spec.requires)}")


def _kind_problems(post, all_kinds) -> list[str]:
    """Fields a post's kind demands but has not been given."""
    name = post.meta.get("post_kind")
    spec = all_kinds.get(name) if name else None
    if spec is None:
        return []
    missing = [f for f in spec.requires if not str(post.meta.get(f) or "").strip()]
    return [f"post_kind '{name}' requires '{f}'" for f in missing]


@cli.command()
@click.argument("slug", required=False)
def check(slug):
    """Validate front matter before anything gets published."""
    cfg = _load()
    posts = (
        [post_mod.find(cfg.site.content_dir, slug)]
        if slug
        else post_mod.discover(cfg.site.content_dir)
    )

    all_kinds = cfg.kinds
    failures = 0
    for post in posts:
        problems = post.validate()
        problems += _kind_problems(post, all_kinds)
        if problems:
            failures += 1
            click.secho(f"✗ {post.slug}", fg="red")
            for problem in problems:
                click.echo(f"    {problem}")
        else:
            state = "draft" if post.is_draft else "ready"
            click.secho(f"✓ {post.slug} ({state})", fg="green")

    if failures:
        raise click.ClickException(f"{failures} post(s) need fixing")
    click.echo(f"\n{len(posts)} post(s) checked, all valid.")


@cli.command(name="import")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--slug", default=None, help="Override the derived slug.")
@click.option("--category", "-c", default="code-review", show_default=True)
@click.option("--series", "-s", default="")
@click.option("--order", "-n", type=int, default=None)
@click.option("--subject", "-u", multiple=True)
@click.option("--tag", "-t", multiple=True)
@click.option("--repo", default="", help="Repository the run analysed.")
@click.option("--copy-video", is_flag=True, help="Copy the mp4 into the bundle.")
@click.option("--force", is_flag=True, help="Overwrite an existing bundle.")
def import_run(run_dir, slug, category, series, order, subject, tag, repo,
               copy_video, force):
    """Adopt a repocast output directory as a post."""
    cfg = _load()
    patterns = cfg.repocast_patterns
    try:
        post = ingest_mod.ingest(
            run_dir,
            cfg.site.content_dir,
            slug=slug,
            category=category,
            series=series,
            series_order=order,
            subjects=subject,
            tags=tag,
            repo=repo,
            patterns=patterns,
            copy_video=copy_video,
            force=force,
        )
    except ingest_mod.IngestError as exc:
        raise click.ClickException(str(exc)) from exc

    click.secho(f"Imported {post.slug}", fg="green")
    click.echo(f"  {post.path}")
    for key in ("video_file", "short_file", "cover"):
        if post.meta.get(key):
            click.echo(f"  {key}: {post.meta[key]}")
    problems = post.validate()
    if problems:
        click.secho("  needs attention before publishing:", fg="yellow")
        for problem in problems:
            click.echo(f"    {problem}")


# --------------------------------------------------------------------------
# building and deploying
# --------------------------------------------------------------------------


@cli.command()
@click.option("--drafts", is_flag=True, help="Include drafts.")
def build(drafts):
    """Build the site locally, including the search index."""
    cfg = _load()
    command = ["hugo", "--minify", "--gc"]
    if drafts:
        command.append("--buildDrafts")
    _run(command, cwd=cfg.site.root)
    _run(
        [
            "npx", "-y", "pagefind@latest",
            "--site", "public",
            "--root-selector", "article.post-single, main.main",
            "--output-subdir", "pagefind",
        ],
        cwd=cfg.site.root,
    )
    click.echo(f"Built into {cfg.site.root / 'public'}")


@cli.command()
def preview():
    """Serve the site locally with drafts visible."""
    cfg = _load()
    _run(["hugo", "server", "--buildDrafts", "--navigateToChanged"], cwd=cfg.site.root)


@cli.command()
@click.argument("slug")
@click.option("--message", "-m", default="", help="Commit message.")
def deploy(slug, message):
    """Undraft a post, commit and push. GitHub Actions does the rest."""
    cfg = _load()
    post = post_mod.find(cfg.site.content_dir, slug)

    problems = post.validate() + _kind_problems(post, cfg.kinds)
    if problems:
        for problem in problems:
            click.echo(f"  {problem}")
        raise click.ClickException("Fix the front matter first")

    if post.is_draft:
        post.set("draft", False)
        post.set("date", dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    post.set("canonical", post.url(cfg.site.base_url))
    post.save()

    _run(["git", "add", "-A"])
    _run(["git", "commit", "-m", message or f"post: {post.title}"])
    _run(["git", "push", cfg.git_remote, cfg.git_branch])

    click.secho(f"Pushed. Live shortly at {post.url(cfg.site.base_url)}", fg="green")


# --------------------------------------------------------------------------
# syndication
# --------------------------------------------------------------------------


@cli.command()
@click.argument("slug")
@click.option(
    "--to",
    default="",
    help="Comma-separated targets. Defaults to whatever the post opts into.",
)
@click.option("--dry-run", is_flag=True, help="Show what would happen, change nothing.")
def syndicate(slug, to, dry_run):
    """Cross-post to dev.to, Hashnode, Medium and YouTube."""
    cfg = _load()
    post = post_mod.find(cfg.site.content_dir, slug)
    state = State(cfg.state_file)

    if to:
        wanted = [t.strip() for t in to.split(",") if t.strip()]
    else:
        wanted = [t for t in ORDER if post.wants(t)]

    if not wanted:
        click.echo("No targets selected. Set devto/hashnode/medium/youtube in "
                   "the post's front matter, or pass --to.")
        return

    for name in wanted:
        target_cfg = cfg.target(name)
        if not target_cfg.enabled and not dry_run:
            click.secho(f"{name:<10} skipped   (disabled in apoapsis.toml)", fg="yellow")
            continue

        target = REGISTRY.get(name)
        if target is None:
            click.secho(f"{name:<10} unknown target", fg="red")
            continue

        try:
            result = target.push(post, cfg, target_cfg, state, dry_run=dry_run)
            colour = {"created": "green", "updated": "cyan", "manual": "yellow"}.get(
                result.action, None
            )
            click.secho(str(result), fg=colour)
        except (SyndicationError, ConfigError) as exc:
            click.secho(f"{name:<10} failed    {exc}", fg="red")

    if not dry_run:
        _write_back_links(post, state)


def _write_back_links(post: Post, state: State) -> None:
    """Record every external copy in the post's own front matter.

    This is what makes the site the hub: once the links live in front matter,
    the next build renders an "Also on" row pointing at every platform, and
    the canonical post is the one place that knows where all the copies are.
    """
    links = {}
    for name, record in state.entry(post.slug).items():
        url = record.get("url")
        if url:
            links[name] = url

    if links and links != (post.meta.get("links") or {}):
        post.set("links", links)
        post.save()
        click.secho(
            f"\nUpdated front matter with {len(links)} outbound link(s). "
            "Commit and push to surface them on the site.",
            fg="cyan",
        )


@cli.command()
@click.argument("slug")
@click.option("--dry-run", is_flag=True)
def video(slug, dry_run):
    """Render a narrated video for a post."""
    cfg = _load()
    post = post_mod.find(cfg.site.content_dir, slug)
    try:
        output = video_mod.render(post, cfg, cfg.target("youtube"), dry_run=dry_run)
    except video_mod.VideoError as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(f"Video ready: {output}", fg="green")


@cli.command()
@click.argument("slug")
@click.option("-m", "--message", default="")
@click.option("--with-video", is_flag=True, help="Render and upload a video too.")
def publish(slug, message, with_video):
    """Deploy, then syndicate — the whole pipeline in one command."""
    ctx = click.get_current_context()
    ctx.invoke(deploy, slug=slug, message=message)
    if with_video:
        ctx.invoke(video, slug=slug, dry_run=False)
    ctx.invoke(syndicate, slug=slug, to="", dry_run=False)


@cli.command()
@click.argument("slug", required=False)
def status(slug):
    """Show where each post has been published."""
    cfg = _load()
    state = State(cfg.state_file)
    data = state.all()

    if slug:
        data = {slug: data.get(slug, {})}

    if not data:
        click.echo("Nothing published yet.")
        return

    for post_slug, targets in sorted(data.items()):
        click.secho(post_slug, bold=True)
        for name, record in sorted(targets.items()):
            url = record.get("url") or record.get("pending_import", "—")
            click.echo(f"  {name:<10} {url}")


def main() -> None:
    try:
        cli()
    except (PostError, ConfigError) as exc:
        click.secho(str(exc), fg="red", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
