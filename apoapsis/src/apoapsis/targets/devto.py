"""dev.to via the Forem v1 API.

Free, no approval process, and it accepts `canonical_url` — which is the whole
point of syndicating: search engines keep crediting your own domain.
"""

from __future__ import annotations

import time

import requests

from ..config import Config, TargetConfig
from ..post import Post
from ..state import State
from .base import Result, SyndicationError

API = "https://dev.to/api"
HEADERS_BASE = {
    "accept": "application/vnd.forem.api-v1+json",
    "content-type": "application/json",
    "user-agent": "apoapsis/1.0",
}


class DevTo:
    name = "devto"

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

        article = {
            "title": post.title,
            "body_markdown": post.syndication_markdown(config.site.base_url),
            "published": not target_config.options.get("draft_first", True),
            "canonical_url": canonical,
            "description": str(post.meta.get("summary", ""))[:250],
            # dev.to caps tags at 4 and wants lowercase alphanumerics.
            "tags": [str(t) for t in post.tags][:4],
        }

        series = post.series
        if series:
            article["series"] = series[0]

        cover = post.meta.get("cover")
        if isinstance(cover, dict):
            cover = cover.get("image")
        if isinstance(cover, str) and cover.startswith("http"):
            article["main_image"] = cover

        existing_id = state.get(post.slug, self.name, "id")

        if dry_run:
            verb = "update" if existing_id else "create"
            return Result(self.name, "skipped", detail=f"dry run — would {verb}")

        headers = {**HEADERS_BASE, "api-key": target_config.secret("api_key_env")}

        if existing_id:
            payload = {"article": article}
            data = self._request(
                "PUT", f"{API}/articles/{existing_id}", headers, payload
            )
            state.record(post.slug, self.name, id=data["id"], url=data.get("url", ""))
            return Result(self.name, "updated", data.get("url", ""))

        data = self._request("POST", f"{API}/articles", headers, {"article": article})
        state.record(
            post.slug,
            self.name,
            id=data["id"],
            url=data.get("url", ""),
            canonical=canonical,
        )
        detail = "draft — review it at dev.to/dashboard" if not article["published"] else ""
        return Result(self.name, "created", data.get("url", ""), detail)

    # ------------------------------------------------------------------

    @staticmethod
    def _request(method: str, url: str, headers: dict, payload: dict) -> dict:
        """dev.to rate-limits creation aggressively; back off and retry."""
        delay = 5.0
        for attempt in range(4):
            response = requests.request(
                method, url, headers=headers, json=payload, timeout=30
            )

            if response.status_code == 429:
                # Retry-After is sometimes seconds, sometimes an HTTP date.
                # Parsing the date is not worth it — fall back to backoff.
                header = response.headers.get("Retry-After", "")
                wait = float(header) if header.isdigit() else delay
                time.sleep(min(wait, 120))
                delay *= 2
                continue

            if response.status_code >= 400:
                raise SyndicationError(
                    f"dev.to {method} {url} returned {response.status_code}: "
                    f"{response.text[:400]}"
                )

            return response.json()

        raise SyndicationError("dev.to kept rate-limiting after 4 attempts")
