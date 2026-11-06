"""
Unit tests for ChatApiSpacesSource (issue #89).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("google.auth")

from google_chat_mcp.chat.client import ChatAPIError  # noqa: E402
from google_chat_mcp.settings.chat_spaces import ChatApiSpacesSource  # noqa: E402
from google_chat_mcp.settings.mock import ALICE  # noqa: E402
from google_chat_mcp.settings.models import Account, Space  # noqa: E402

_ACCOUNT = Account(sub=ALICE.sub, email=ALICE.email, hd="example.com", access_token="tok-123")

_SPACE_A = {
    "name": "spaces/AAABBBCCC",
    "displayName": "Team General",
    "spaceType": "SPACE",
}
_SPACE_B = {
    "name": "spaces/DDDEEEFFF",
    "displayName": "Announcements",
    "spaceType": "SPACE",
}
_DM = {
    "name": "spaces/DM00001",
    "displayName": "",
    "spaceType": "DIRECT_MESSAGE",
}


def _mock_client(list_pages=None, get_result=None, get_error=None):
    """Return a ChatApiSpacesSource whose underlying SpacesClient is mocked."""
    source = ChatApiSpacesSource()

    client = MagicMock()
    if list_pages is not None:
        # list() returns a flat list (SpacesClient already paginates).
        client.list.return_value = list_pages
    if get_error is not None:
        client.get.side_effect = get_error
    elif get_result is not None:
        client.get.return_value = get_result

    source._client = lambda account: client
    return source, client


# ---------------------------------------------------------------------------
# list_spaces


def test_list_spaces_maps_raw_dicts_to_space_objects():
    source, _ = _mock_client(list_pages=[_SPACE_A, _SPACE_B, _DM])
    spaces = source.list_spaces(_ACCOUNT)
    assert len(spaces) == 3
    assert spaces[0] == Space("spaces/AAABBBCCC", "Team General", "SPACE")
    assert spaces[1] == Space("spaces/DDDEEEFFF", "Announcements", "SPACE")
    assert spaces[2] == Space("spaces/DM00001", "", "DIRECT_MESSAGE")


def test_list_spaces_uses_page_size_1000():
    source, client = _mock_client(list_pages=[])
    source.list_spaces(_ACCOUNT)
    client.list.assert_called_once_with(page_size=1000)


def test_list_spaces_empty_result():
    source, _ = _mock_client(list_pages=[])
    assert source.list_spaces(_ACCOUNT) == []


def test_list_spaces_missing_optional_fields():
    source, _ = _mock_client(list_pages=[{"name": "spaces/X"}])
    spaces = source.list_spaces(_ACCOUNT)
    assert spaces[0] == Space("spaces/X", "", "")


# ---------------------------------------------------------------------------
# get_space


def test_get_space_returns_space_on_success():
    source, _ = _mock_client(get_result=_SPACE_A)
    space = source.get_space(_ACCOUNT, "spaces/AAABBBCCC")
    assert space == Space("spaces/AAABBBCCC", "Team General", "SPACE")


def test_get_space_returns_none_on_401():
    source, _ = _mock_client(get_error=ChatAPIError(401, "Unauthorized"))
    assert source.get_space(_ACCOUNT, "spaces/AAABBBCCC") is None


def test_get_space_returns_none_on_403():
    source, _ = _mock_client(get_error=ChatAPIError(403, "Forbidden"))
    assert source.get_space(_ACCOUNT, "spaces/AAABBBCCC") is None


def test_get_space_returns_none_on_404():
    source, _ = _mock_client(get_error=ChatAPIError(404, "Not Found"))
    assert source.get_space(_ACCOUNT, "spaces/AAABBBCCC") is None


def test_get_space_reraises_429():
    source, _ = _mock_client(get_error=ChatAPIError(429, "Too Many Requests"))
    with pytest.raises(ChatAPIError) as exc_info:
        source.get_space(_ACCOUNT, "spaces/AAABBBCCC")
    assert exc_info.value.status_code == 429


def test_get_space_reraises_500():
    source, _ = _mock_client(get_error=ChatAPIError(500, "Server Error"))
    with pytest.raises(ChatAPIError) as exc_info:
        source.get_space(_ACCOUNT, "spaces/AAABBBCCC")
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# _client uses the account's access_token


def test_client_uses_account_access_token():
    """_client() builds credentials from account.access_token."""
    source = ChatApiSpacesSource()
    captured = {}

    def fake_credentials(*, token):
        captured["token"] = token
        return MagicMock()

    with patch("google_chat_mcp.settings.chat_spaces.Credentials", side_effect=fake_credentials):
        with patch("google_chat_mcp.settings.chat_spaces.AuthorizedSession"):
            with patch("google_chat_mcp.settings.chat_spaces.SpacesClient"):
                source._client(_ACCOUNT)

    assert captured["token"] == "tok-123"
