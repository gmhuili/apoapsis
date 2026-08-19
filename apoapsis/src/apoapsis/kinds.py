"""Post kinds.

The site is not only code review. A kind bundles together the default
category, the skeleton outline, and any extra front matter a content type
needs — so adding "tech forecast" or "retrospective" is a config edit rather
than a code change.

Everything here is a default. `[kinds.<name>]` in apoapsis.toml overrides any
field, and declaring a kind that is not listed below simply creates a new one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Kind:
    name: str
    category: str
    description: str = ""
    outline: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    # Front-matter keys this kind adds, with their placeholder values.
    extra: dict = field(default_factory=dict)
    # Front-matter keys that must be filled in before the post can publish.
    requires: tuple[str, ...] = ()
    needs_repo: bool = False
    # Whether `{{< video >}}` is scaffolded into the body.
    video: bool = True


DEFAULT_KINDS: dict[str, Kind] = {
    "code-review": Kind(
        name="code-review",
        category="code-review",
        description="A close reading of a specific codebase.",
        outline=(
            "The question",
            "How the code is organised",
            "Reading the hot path",
            "What surprised me",
            "What I would change",
        ),
        needs_repo=True,
    ),
    "essay": Kind(
        name="essay",
        category="essay",
        description="An argument about engineering practice.",
        outline=(
            "The claim",
            "Why the usual answer is incomplete",
            "The case",
            "Where this breaks down",
            "What follows from it",
        ),
    ),
    "forecast": Kind(
        name="forecast",
        category="forecast",
        description="A dated, falsifiable prediction about where a technology goes.",
        outline=(
            "The prediction",
            "What has to be true for this to happen",
            "Current evidence",
            "What would falsify it",
            "How I will score this",
        ),
        extra={
            "horizon": "",       # ISO date the prediction resolves by
            "confidence": "",    # e.g. "60%" — state it, then be held to it
            "review_on": "",     # ISO date to revisit; drives the review queue
            "resolved": "",      # "correct" | "wrong" | "partial", set later
        },
        requires=("horizon", "confidence", "review_on"),
        video=False,
    ),
    "teardown": Kind(
        name="teardown",
        category="teardown",
        description="Taking a system or protocol apart to see how it works.",
        outline=(
            "What it claims to do",
            "The architecture",
            "The interesting mechanism",
            "Failure modes",
            "Verdict",
        ),
    ),
    "explainer": Kind(
        name="explainer",
        category="explainer",
        description="Teaching one concept properly.",
        outline=(
            "The problem this solves",
            "The idea",
            "Worked example",
            "Common misunderstandings",
            "Where to go next",
        ),
    ),
    "retrospective": Kind(
        name="retrospective",
        category="retrospective",
        description="Scoring an earlier forecast against what actually happened.",
        outline=(
            "What I predicted",
            "What happened",
            "Where I was wrong, and why",
            "What I am updating",
        ),
        extra={"scores": []},   # slugs of the forecasts being reviewed
        video=False,
    ),
    "note": Kind(
        name="note",
        category="note",
        description="A short observation that does not need an outline.",
        outline=(),
        video=False,
    ),
}


def build(overrides: dict | None = None) -> dict[str, Kind]:
    """Merge apoapsis.toml's [kinds.*] over the built-in defaults."""
    kinds = dict(DEFAULT_KINDS)

    for name, block in (overrides or {}).items():
        if not isinstance(block, dict):
            continue
        base = kinds.get(name)
        kinds[name] = Kind(
            name=name,
            category=block.get("category", base.category if base else name),
            description=block.get("description", base.description if base else ""),
            outline=tuple(block.get("outline", base.outline if base else ())),
            tags=tuple(block.get("tags", base.tags if base else ())),
            extra=dict(block.get("extra", base.extra if base else {})),
            requires=tuple(block.get("requires", base.requires if base else ())),
            needs_repo=bool(block.get("needs_repo", base.needs_repo if base else False)),
            video=bool(block.get("video", base.video if base else True)),
        )

    return kinds


def resolve(kinds: dict[str, Kind], name: str) -> Kind:
    if name in kinds:
        return kinds[name]
    known = ", ".join(sorted(kinds))
    raise KeyError(f"Unknown kind '{name}'. Known kinds: {known}")
