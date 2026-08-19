"""YouTube upload via Data API v3.

Quota note: a project gets 10,000 units/day by default and `videos.insert`
costs 1,600, so roughly six uploads a day before you need a quota increase.
That is far more than a review blog needs.

Auth is the installed-app OAuth flow. The first run opens a browser once and
caches a refresh token; every run after that is non-interactive.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config, TargetConfig
from ..post import Post
from ..state import State
from .base import Result, SyndicationError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTube:
    name = "youtube"

    def push(
        self,
        post: Post,
        config: Config,
        target_config: TargetConfig,
        state: State,
        *,
        dry_run: bool = False,
    ) -> Result:
        video_path = self._resolve_video(post, target_config)
        if video_path is None:
            return Result(
                self.name,
                "skipped",
                detail="no rendered video — run `apo video` first",
            )

        if dry_run:
            return Result(
                self.name, "skipped", detail=f"dry run — would upload {video_path.name}"
            )

        if state.get(post.slug, self.name, "id"):
            return Result(
                self.name,
                "skipped",
                detail="already uploaded; delete the state entry to re-upload",
            )

        youtube = self._client(target_config)
        body = {
            "snippet": {
                "title": self._title(post, target_config),
                "description": self._description(post, config),
                "tags": [str(t) for t in (post.tags + post.subjects)][:15],
                "categoryId": str(target_config.options.get("category_id", 28)),
            },
            "status": {
                "privacyStatus": target_config.options.get("privacy", "private"),
                "selfDeclaredMadeForKids": False,
            },
        }

        from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

        media = MediaFileUpload(
            str(video_path), chunksize=8 * 1024 * 1024, resumable=True
        )
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  uploading… {int(status.progress() * 100)}%")

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        state.record(post.slug, self.name, id=video_id, url=url)
        return Result(
            self.name,
            "created",
            url,
            f"privacy={body['status']['privacyStatus']}",
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_video(post: Post, target_config: TargetConfig) -> Path | None:
        explicit = post.meta.get("video_file")
        if explicit:
            path = Path(explicit)
            return path if path.is_file() else None

        out_dir = Path(target_config.options.get("render_dir", "build/video"))
        candidate = out_dir / f"{post.slug}.mp4"
        return candidate if candidate.is_file() else None

    @staticmethod
    def _title(post: Post, target_config: TargetConfig) -> str:
        template = target_config.options.get("title_template", "{title}")
        title = template.format(title=post.title, slug=post.slug)
        return title[:100]  # YouTube's hard limit

    @staticmethod
    def _description(post: Post, config: Config) -> str:
        parts = [
            str(post.meta.get("summary", "")).strip(),
            "",
            f"Full write-up: {post.url(config.site.base_url)}",
        ]
        if post.meta.get("repo"):
            parts.append(f"Repository: {post.meta['repo']}")
        if post.series:
            parts.append(f"Part of the series: {post.series[0]}")
        parts += ["", " ".join(f"#{t}" for t in post.tags[:5])]
        return "\n".join(parts)[:5000]

    @staticmethod
    def _client(target_config: TargetConfig):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise SyndicationError(
                "YouTube support needs extra packages: "
                "pip install 'apoapsis[youtube]'"
            ) from exc

        secrets = Path(
            target_config.options.get("client_secrets", "client_secrets.json")
        )
        token_path = Path(target_config.options.get("token_file", ".youtube-token.json"))

        creds = None
        if token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not secrets.is_file():
                    raise SyndicationError(
                        f"{secrets} not found. Create an OAuth client (type: "
                        "Desktop app) in Google Cloud Console, enable the "
                        "YouTube Data API v3, and download the JSON here."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("youtube", "v3", credentials=creds, cache_discovery=False)
