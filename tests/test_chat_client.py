"""
Tests for the Google Chat HTTP client (_BaseSession, SpacesClient, MessagesClient).
All HTTP calls are mocked — no real network.
"""

from unittest.mock import MagicMock, patch

import pytest

from google_chat_mcp.chat.client import _BaseSession, ChatAPIError
from google_chat_mcp.chat.spaces import SpacesClient
from google_chat_mcp.chat.messages import MessagesClient


def _make_base(response_json: dict | list, status_code: int = 200) -> _BaseSession:
    mock_resp = MagicMock()
    mock_resp.ok = status_code < 400
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    mock_resp.text = str(response_json)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp
    mock_session.post.return_value = mock_resp

    return _BaseSession(mock_session)


# --- _BaseSession._get ---


def test_get_200_ritorna_json():
    base = _make_base({"spaces": [{"name": "spaces/X"}]})
    result = base._get("spaces")
    assert result == {"spaces": [{"name": "spaces/X"}]}


def test_get_401_solleva_chat_api_error():
    base = _make_base({}, status_code=401)
    with pytest.raises(ChatAPIError) as exc:
        base._get("spaces")
    assert exc.value.status_code == 401


def test_get_500_solleva_chat_api_error():
    base = _make_base({}, status_code=500)
    with pytest.raises(ChatAPIError) as exc:
        base._get("spaces/A/messages")
    assert exc.value.status_code == 500


def test_get_params_none_filtrati():
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {}
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp
    base = _BaseSession(mock_session)

    base._get("spaces", pageSize=10, pageToken=None)

    _, kwargs = mock_session.get.call_args
    assert "pageToken" not in kwargs.get("params", {})
    assert kwargs["params"]["pageSize"] == 10


# --- _BaseSession._post ---


def test_post_200_ritorna_json():
    base = _make_base({"name": "spaces/A/messages/1"})
    result = base._post("spaces/A/messages", {"text": "ciao"})
    assert result == {"name": "spaces/A/messages/1"}


def test_post_body_passato_come_json():
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {}
    mock_session = MagicMock()
    mock_session.post.return_value = mock_resp
    base = _BaseSession(mock_session)

    base._post("spaces/A/messages", {"text": "hello"})

    _, kwargs = mock_session.post.call_args
    assert kwargs["json"] == {"text": "hello"}


def test_post_4xx_solleva_chat_api_error():
    base = _make_base({}, status_code=403)
    with pytest.raises(ChatAPIError) as exc:
        base._post("spaces/A/messages", {"text": "x"})
    assert exc.value.status_code == 403


# --- SpacesClient.list: pagination ---


def test_spaces_list_paginazione():
    page1 = {"spaces": [{"name": "spaces/A"}], "nextPageToken": "tok1"}
    page2 = {"spaces": [{"name": "spaces/B"}]}

    mock_resp1, mock_resp2 = MagicMock(), MagicMock()
    for resp, data in ((mock_resp1, page1), (mock_resp2, page2)):
        resp.ok = True
        resp.json.return_value = data

    mock_session = MagicMock()
    mock_session.get.side_effect = [mock_resp1, mock_resp2]
    base = _BaseSession(mock_session)

    result = SpacesClient(base).list()
    assert result == [{"name": "spaces/A"}, {"name": "spaces/B"}]
    assert mock_session.get.call_count == 2


# --- MessagesClient.send ---


def test_messages_send_url_e_body_corretti():
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"name": "spaces/A/messages/99"}
    mock_session = MagicMock()
    mock_session.post.return_value = mock_resp
    base = _BaseSession(mock_session)

    result = MessagesClient(base).send("spaces/A", "testo")

    args, kwargs = mock_session.post.call_args
    assert "spaces/A/messages" in args[0]
    assert kwargs["json"] == {"text": "testo"}
    assert result == {"name": "spaces/A/messages/99"}
