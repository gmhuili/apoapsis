"""Hashnode via the GraphQL API at gql.hashnode.com.

Heads-up on cost: Hashnode retired free GraphQL access in May 2026. Both reads
and writes now require a Pro plan on the publication, so this target is
disabled by default — it does not fit a strictly free-of-charge setup. The
implementation is here so it is a config flag away if you ever upgrade.
"""

from __future__ import annotations

import requests

from ..config import Config, TargetConfig
from ..post import Post
from ..state import State
from .base import Result, SyndicationError

ENDPOINT = "https://gql.hashnode.com"

PUBLISH = """
mutation Publish($input: PublishPostInput!) {
  publishPost(input: $input) { post { id slug url } }
}
"""

UPDATE = """
mutation Update($input: UpdatePostInput!) {
  updatePost(input: $input) { post { id slug url } }
}
"""


class Hashnode:
    name = "hashnode"

    def push(
        self,
        post: Post,
        config: Config,
        target_config: TargetConfig,
        state: State,
        *,
        dry_run: bool = False,
    ) -> Result:
        if dry_run:
            return Result(self.name, "skipped", detail="dry run")

        publication_id = target_config.options.get("publication_id")
        if not publication_id:
            raise SyndicationError(
                "Set publication_id under [targets.hashnode] in apoapsis.toml"
            )

        token = target_config.secret("token_env")
        canonical = post.url(config.site.base_url)
        markdown = post.syndication_markdown(config.site.base_url)
        existing_id = state.get(post.slug, self.name, "id")

        if existing_id:
            variables = {
                "input": {
                    "id": existing_id,
                    "title": post.title,
                    "contentMarkdown": markdown,
                    "originalArticleURL": canonical,
                }
            }
            data = self._call(UPDATE, variables, token)["updatePost"]["post"]
            action = "updated"
        else:
            variables = {
                "input": {
                    "publicationId": publication_id,
                    "title": post.title,
                    "contentMarkdown": markdown,
                    "originalArticleURL": canonical,
                    "tags": [
                        {"slug": str(t), "name": str(t)} for t in post.tags[:5]
                    ],
                    "subtitle": str(post.meta.get("summary", ""))[:250],
                }
            }
            data = self._call(PUBLISH, variables, token)["publishPost"]["post"]
            action = "created"

        state.record(post.slug, self.name, id=data["id"], url=data.get("url", ""))
        return Result(self.name, action, data.get("url", ""))

    @staticmethod
    def _call(query: str, variables: dict, token: str) -> dict:
        response = requests.post(
            ENDPOINT,
            # Hashnode wants the bare PAT, not "Bearer <pat>".
            headers={"Content-Type": "application/json", "Authorization": token},
            json={"query": query, "variables": variables},
            timeout=30,
        )
        if response.status_code >= 400:
            raise SyndicationError(
                f"Hashnode returned {response.status_code}: {response.text[:400]}"
            )
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in payload["errors"])
            raise SyndicationError(f"Hashnode GraphQL errors: {messages}")
        return payload["data"]
