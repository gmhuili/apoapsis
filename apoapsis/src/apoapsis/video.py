"""Turn a post into a video, then hand the file to the YouTube target.

This does not render video itself. It builds a narration script from the post
and shells out to whichever renderer you name in `[targets.youtube] renderer`.
If you already have a repo-to-video pipeline, point `renderer` at it and this
becomes a thin adapter.

The renderer is invoked as:

    <renderer> --script <script.md> --repo <repo-url> --out <slug>.mp4

Placeholders in the configured command are substituted instead if you need a
different argument order — see `render_command` in apoapsis.toml.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from .config import Config, TargetConfig
from .post import Post


class VideoError(RuntimeError):
    pass


def narration_script(post: Post, config: Config) -> str:
    """Strip a post down to something a TTS engine can read aloud.

    Code blocks become a spoken cue plus the retained block, so the renderer
    can show the code on screen while the narrator describes it.
    """
    body = post.syndication_markdown(config.site.base_url)

    # Tables and HTML do not narrate well; drop them.
    body = re.sub(r"^\s*\|.*\|\s*$\n?", "", body, flags=re.MULTILINE)
    body = re.sub(r"<[^>]+>", "", body)

    lines = [
        f"# {post.title}",
        "",
        str(post.meta.get("summary", "")).strip(),
        "",
        "---",
        "",
        body,
        "",
        "---",
        "",
        f"Full write-up and the annotated source: {post.url(config.site.base_url)}",
    ]
    return "\n".join(lines)


def render(
    post: Post,
    config: Config,
    target_config: TargetConfig,
    *,
    dry_run: bool = False,
) -> Path:
    out_dir = Path(target_config.options.get("render_dir", "build/video"))
    out_dir.mkdir(parents=True, exist_ok=True)

    script_path = out_dir / f"{post.slug}.script.md"
    script_path.write_text(narration_script(post, config), encoding="utf-8")

    output = out_dir / f"{post.slug}.mp4"

    template = target_config.options.get("render_command")
    if not template:
        renderer = target_config.options.get("renderer")
        if not renderer:
            raise VideoError(
                "No renderer configured. Set `renderer` (or `render_command`) "
                "under [targets.youtube] in apoapsis.toml."
            )
        template = f"{renderer} --script {{script}} --out {{output}}"
        if post.meta.get("repo"):
            template += " --repo {repo}"

    command = template.format(
        script=shlex.quote(str(script_path)),
        output=shlex.quote(str(output)),
        repo=shlex.quote(str(post.meta.get("repo", ""))),
        slug=shlex.quote(post.slug),
        title=shlex.quote(post.title),
    )

    if dry_run:
        print(f"  would run: {command}")
        return output

    print(f"  script:  {script_path}")
    print(f"  running: {command}")
    result = subprocess.run(command, shell=True, check=False)
    if result.returncode != 0:
        raise VideoError(f"Renderer exited with status {result.returncode}")
    if not output.is_file():
        raise VideoError(f"Renderer finished but {output} does not exist")

    return output
