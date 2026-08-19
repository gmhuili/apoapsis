"""Medium — deliberately not automated, and here is why.

Medium stopped issuing integration tokens and stopped allowing new API
integrations. Existing tokens still work, but a new account cannot obtain one,
so there is no honest way to fully automate this.

The working route is Medium's own "Import a story" tool: you give it a URL, it
pulls in the content and sets rel=canonical back to the source. That preserves
your SEO and is eligible for the Partner Program. This target prints the exact
import link so the manual step takes about ten seconds.

If you do hold a legacy token, set `token_env` and `legacy_api = true` and the
API path below will be used instead.
"""

from __future__ import annotations

import requests

from ..config import Config, TargetConfig
from ..post import Post
from ..state import State
from .base import Result, SyndicationError

API = "https://api.medium.com/v1"
IMPORT_URL = "https://medium.com/p/import"


class Medium:
    name = "medium"

    def push(
        self,
        post: Post,
        config: Config,
        target_config: TargetConfig,
        state: State,
        *,
        dry_run: bool = False,
    ) -> Result:
        canonical = post.url(config.site.base_url)

        if not target_config.options.get("legacy_api", False):
            state.record(post.slug, self.name, pending_import=canonical)
            return Result(
                self.name,
                "manual",
                f"{IMPORT_URL}?url={canonical}",
                "paste the canonical URL into Medium's importer",
            )

        if dry_run:
            return Result(self.name, "skipped", detail="dry run")

        token = target_config.secret("token_env")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        me = requests.get(f"{API}/me", headers=headers, timeout=30)
        if me.status_code >= 400:
            raise SyndicationError(
                "Medium rejected the legacy token. Tokens issued before 2025 "
                "still work; new ones cannot be created."
            )
        user_id = me.json()["data"]["id"]

        payload = {
            "title": post.title,
            "contentFormat": "markdown",
            "content": f"# {post.title}\n\n"
                       + post.syndication_markdown(config.site.base_url),
            "canonicalUrl": canonical,
            "tags": [str(t) for t in post.tags[:5]],
            "publishStatus": target_config.options.get("publish_status", "draft"),
        }

        response = requests.post(
            f"{API}/users/{user_id}/posts",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if response.status_code >= 400:
            raise SyndicationError(
                f"Medium returned {response.status_code}: {response.text[:400]}"
            )

        data = response.json()["data"]
        state.record(post.slug, self.name, id=data["id"], url=data["url"])
        return Result(self.name, "created", data["url"])
