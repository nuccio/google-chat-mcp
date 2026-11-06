"""
Chat API SpacesSource for the settings app (step 4 of
docs/remote/04-settings-server-design.md).

Needs the `remote` extra. Never imported by mock.py or local mode.
"""

from __future__ import annotations

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from ..chat.client import ChatAPIError, _BaseSession
from ..chat.spaces import SpacesClient
from .models import Account, Space


class ChatApiSpacesSource:
    """
    SpacesSource backed by the Chat API, using the session's access token.

    Both methods are synchronous; NiceGUI runs sync event handlers in a thread
    pool so blocking I/O is fine here.
    """

    def list_spaces(self, account: Account) -> list[Space]:
        client = self._client(account)
        raw = client.list(page_size=1000)
        return [_to_space(s) for s in raw]

    def get_space(self, account: Account, space_name: str) -> Space | None:
        try:
            return _to_space(self._client(account).get(space_name))
        except ChatAPIError as exc:
            if exc.status_code in (401, 403, 404):
                return None
            raise

    def _client(self, account: Account) -> SpacesClient:
        creds = Credentials(token=account.access_token)
        return SpacesClient(_BaseSession(AuthorizedSession(creds)))


def _to_space(raw: dict) -> Space:
    return Space(
        name=raw["name"],
        display_name=raw.get("displayName", ""),
        space_type=raw.get("spaceType", ""),
    )
