from google.auth.transport.requests import AuthorizedSession

from ..auth import load_credentials
from .spaces import SpacesClient
from .messages import MessagesClient
from .members import MembersClient

_BASE_URL = "https://chat.googleapis.com/v1"


class ChatAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"Google Chat API {status_code}: {body}")


class _BaseSession:
    def __init__(self, session: AuthorizedSession):
        self._session = session

    def _get(self, path: str, **params) -> dict:
        r = self._session.get(
            f"{_BASE_URL}/{path}",
            params={k: v for k, v in params.items() if v is not None},
        )
        if not r.ok:
            raise ChatAPIError(r.status_code, r.text)
        return r.json()

    def _post(self, path: str, body: dict, params: dict | None = None) -> dict:
        r = self._session.post(f"{_BASE_URL}/{path}", json=body, params=params)
        if not r.ok:
            raise ChatAPIError(r.status_code, r.text)
        return r.json()


class ChatClient:
    def __init__(self):
        creds = load_credentials()
        base = _BaseSession(AuthorizedSession(creds))
        self.spaces = SpacesClient(base)
        self.messages = MessagesClient(base)
        self.members = MembersClient(base)
