"""LINE Messaging API: replies and quota-consuming pushes are distinct operations."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)


class LineAPIError(RuntimeError):
    pass


class LineAPI:
    BASE = "https://api.line.me/v2/bot"

    def __init__(self, token: str, *, session=None, timeout=(3, 8), sleep=time.sleep):
        self.token = token
        self.session = session
        self.timeout = timeout
        self.sleep = sleep

    def _request(self, method, path, *, payload=None, retry_key=None, params=None):
        if not self.token:
            raise LineAPIError("CHANNEL_ACCESS_TOKEN is not configured.")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key
        # Replies are single-attempt: an ambiguous timeout must not reuse an expired token.
        attempts = 3 if method == "GET" or retry_key else 1
        request = self.session.request if self.session is not None else requests.request
        for attempt in range(attempts):
            try:
                response = request(
                    method,
                    self.BASE + path,
                    headers=headers,
                    json=payload,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt + 1 < attempts:
                    self.sleep(0.25 * (2**attempt))
                    continue
                raise LineAPIError(
                    "LINE request failed; inspect connectivity or service availability."
                ) from None
            if (
                response.status_code == 409
                and retry_key
                and response.headers.get("x-line-accepted-request-id")
            ):
                return {}
            if response.status_code >= 500 and attempt + 1 < attempts:
                self.sleep(0.25 * (2**attempt))
                continue
            if not 200 <= response.status_code < 300:
                # Never log the request body, token, or an API echo of confidential content.
                raise LineAPIError(
                    f"LINE API returned HTTP {response.status_code} for {path}."
                )
            try:
                return response.json()
            except ValueError:
                raise LineAPIError("LINE returned a non-JSON response.") from None
        raise LineAPIError("LINE request exhausted retries.")

    @staticmethod
    def text(text):
        return {"type": "text", "text": str(text)[:5000] or " "}

    @staticmethod
    def image(original, preview):
        if any(
            not url.startswith("https://") or not url.endswith(".jpg")
            for url in (original, preview)
        ):
            raise ValueError("LINE image URLs must be HTTPS JPEG URLs.")
        return {
            "type": "image",
            "originalContentUrl": original,
            "previewImageUrl": preview,
        }

    @staticmethod
    def _messages(messages):
        if not isinstance(messages, list) or not 1 <= len(messages) <= 5:
            raise ValueError("LINE requires 1–5 messages per request.")
        return messages

    def reply(self, reply_token, messages):
        if not reply_token:
            raise ValueError("An interactive response requires a reply token.")
        return self._request(
            "POST",
            "/message/reply",
            payload={"replyToken": reply_token, "messages": self._messages(messages)},
        )

    def push(self, recipient_id, messages):
        if not recipient_id:
            raise ValueError("Scheduled delivery requires GROUP_ID or USER_ID.")
        return self._request(
            "POST",
            "/message/push",
            payload={"to": recipient_id, "messages": self._messages(messages)},
            retry_key=str(uuid.uuid4()),
        )

    def quota(self):
        limit = self._request("GET", "/message/quota")
        usage = self._request("GET", "/message/quota/consumption")
        total = int(usage["totalUsage"])
        maximum = int(limit["value"]) if limit.get("type") == "limited" else None
        return {
            "limit": maximum,
            "consumed": total,
            "remaining": max(0, maximum - total) if maximum is not None else None,
        }

    def group_reach(self, group_id):
        if not group_id:
            return None
        # This API exposes membership, not each member's block status.
        return int(self._request("GET", f"/group/{group_id}/members/count")["count"])

    def followers(self, date=None):
        """Daily follower insights; targetedReaches is for followers, not a group."""
        date = date or (
            datetime.now(timezone(timedelta(hours=9))) - timedelta(days=1)
        ).strftime("%Y%m%d")
        return self._request("GET", "/insight/followers", params={"date": date})

    def follower_ids(self):
        """Available only for eligible verified/premium accounts; errors remain explicit."""
        ids = []
        cursor = None
        while True:
            params = {"limit": 1000}
            if cursor:
                params["start"] = cursor
            page = self._request("GET", "/followers/ids", params=params)
            ids.extend(page["userIds"])
            cursor = page.get("next")
            if not cursor:
                return ids

    def status_text(self, group_id, user_id=""):
        lines = []
        try:
            quota = self.quota()
            lines += [
                f"Monthly limit: {quota['limit'] if quota['limit'] is not None else 'unlimited'}",
                f"Consumed: {quota['consumed']}",
                f"Remaining: {quota['remaining'] if quota['remaining'] is not None else 'unlimited'}",
            ]
        except (LineAPIError, ValueError, KeyError, TypeError) as exc:
            lines.append(f"Quota unavailable: {exc}")
        if group_id:
            try:
                members = self.group_reach(group_id)
                lines.append(
                    f'Group members: {members if members is not None else "unavailable"} (upper bound; unblocked reach unavailable)'
                )
            except (LineAPIError, ValueError, KeyError, TypeError):
                lines.append("Group reach unavailable for this account/group.")
        elif user_id:
            lines.append(
                "Private delivery: up to 1 user (requires friendship and unblocked bot)."
            )
        else:
            lines.append("Delivery destination is not configured; use check-id.")
        return "\n".join(lines)
