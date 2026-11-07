"""Every REST route reaches the tool it documents with the arguments the
caller sent: query, body and path parameters merged the way docs/http-api.md
says. The tools themselves are stubbed out; their behaviour is tested
elsewhere."""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(base_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, list]]:
    import app.server as server_mod
    from app.client import TelethonHolder
    from app.config import Config

    class OfflineHolder(TelethonHolder):
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_run(holder: Any, tool_name: str, raw: dict[str, Any]) -> Any:
        calls.append((tool_name, dict(raw)))
        return {"tool": tool_name}

    monkeypatch.setattr(server_mod, "TelethonHolder", OfflineHolder)
    monkeypatch.setattr(server_mod, "_run", fake_run)
    with TestClient(server_mod.build_app(Config.from_env())) as client:
        yield client, calls


# (method, path, query, json body, tool, arguments the tool receives)
ROUTES = [
    ("GET", "/api/me", None, None, "get_me", {}),
    ("GET", "/api/throttle/status", None, None, "throttle_status", {}),
    ("GET", "/api/account/health", None, None, "account_health", {}),
    ("GET", "/api/entities", {"chat": "@x"}, None, "get_entity", {"chat": "@x"}),
    ("POST", "/api/entities/bulk", None, {"chats": ["@a"]}, "bulk_resolve", {"chats": ["@a"]}),
    ("GET", "/api/dialogs", {"limit": "5"}, None, "get_dialogs", {"limit": "5"}),
    ("GET", "/api/messages", {"chat": "me"}, None, "get_messages", {"chat": "me"}),
    (
        "POST",
        "/api/messages",
        None,
        {"chat": "me", "text": "hi"},
        "send_message",
        {"chat": "me", "text": "hi"},
    ),
    (
        "POST",
        "/api/messages",
        None,
        {"chat": "me", "file_url": "https://f.invalid/a.png"},
        "send_file",
        {"chat": "me", "file_url": "https://f.invalid/a.png"},
    ),
    (
        "POST",
        "/api/messages/forward",
        None,
        {"from_chat": "a", "to_chat": "b", "message_ids": [1]},
        "forward_messages",
        {"from_chat": "a", "to_chat": "b", "message_ids": [1]},
    ),
    ("POST", "/api/messages/read", None, {"chat": "me"}, "mark_read", {"chat": "me"}),
    (
        "DELETE",
        "/api/messages",
        None,
        {"chat": "me", "message_ids": [1]},
        "delete_messages",
        {"chat": "me", "message_ids": [1]},
    ),
    (
        "GET",
        "/api/messages/7",
        {"chat": "me"},
        None,
        "get_message",
        {"chat": "me", "message_id": 7},
    ),
    (
        "PATCH",
        "/api/messages/7",
        None,
        {"chat": "me", "text": "x"},
        "edit_message",
        {"chat": "me", "text": "x", "message_id": 7},
    ),
    (
        "POST",
        "/api/messages/7/pin",
        None,
        {"chat": "me"},
        "pin_message",
        {"chat": "me", "message_id": 7},
    ),
    (
        "POST",
        "/api/messages/7/unpin",
        None,
        {"chat": "me"},
        "unpin_message",
        {"chat": "me", "message_id": 7},
    ),
    (
        "POST",
        "/api/messages/7/reactions",
        None,
        {"chat": "me", "emoji": "x"},
        "set_reaction",
        {"chat": "me", "emoji": "x", "message_id": 7},
    ),
    (
        "DELETE",
        "/api/messages/7/reactions",
        None,
        {"chat": "me"},
        "remove_reaction",
        {"chat": "me", "message_id": 7},
    ),
    ("GET", "/api/participants", {"chat": "@g"}, None, "get_participants", {"chat": "@g"}),
    ("POST", "/api/chats", None, {"title": "t"}, "create_group", {"title": "t"}),
    ("DELETE", "/api/chats", None, {"chat": "@g"}, "delete_chat", {"chat": "@g"}),
    ("POST", "/api/chats/join", None, {"chat": "@g"}, "join_chat", {"chat": "@g"}),
    ("POST", "/api/chats/leave", None, {"chat": "@g"}, "leave_chat", {"chat": "@g"}),
    ("POST", "/api/chats/invite", None, {"invite": "+abc"}, "join_via_invite", {"invite": "+abc"}),
    ("GET", "/api/chats/@g/linked", None, None, "get_linked_chat", {"chat": "@g"}),
    (
        "POST",
        "/api/chats/@g/admin/ban",
        None,
        {"user": "@u"},
        "ban_user",
        {"user": "@u", "chat": "@g"},
    ),
    (
        "POST",
        "/api/chats/@g/admin/unban",
        None,
        {"user": "@u"},
        "unban_user",
        {"user": "@u", "chat": "@g"},
    ),
    (
        "POST",
        "/api/chats/@g/admin/kick",
        None,
        {"user": "@u"},
        "kick_user",
        {"user": "@u", "chat": "@g"},
    ),
    (
        "POST",
        "/api/chats/@g/admin/promote",
        None,
        {"user": "@u"},
        "promote_user",
        {"user": "@u", "chat": "@g"},
    ),
    (
        "POST",
        "/api/chats/@g/admin/demote",
        None,
        {"user": "@u"},
        "demote_user",
        {"user": "@u", "chat": "@g"},
    ),
    (
        "POST",
        "/api/polls",
        None,
        {"chat": "@g", "question": "q", "options": ["a", "b"]},
        "create_poll",
        {"chat": "@g", "question": "q", "options": ["a", "b"]},
    ),
    (
        "POST",
        "/api/polls/7/vote",
        None,
        {"chat": "@g", "options": [0]},
        "vote_poll",
        {"chat": "@g", "options": [0], "message_id": 7},
    ),
    (
        "GET",
        "/api/polls/7/results",
        {"chat": "@g"},
        None,
        "get_poll_results",
        {"chat": "@g", "message_id": 7},
    ),
]


@pytest.mark.parametrize(("method", "path", "query", "body", "tool", "arguments"), ROUTES)
def test_each_route_runs_its_tool_with_the_callers_arguments(
    api: tuple[TestClient, list],
    method: str,
    path: str,
    query: Any,
    body: Any,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    client, calls = api
    response = client.request(method, path, params=query, json=body)
    assert response.status_code == 200, response.text
    assert calls == [(tool, arguments)]


def test_every_tool_except_download_media_has_a_route() -> None:
    """A tool added to the registry must also be added to ROUTES above and to
    the docs; download_media has its own streaming route, tested below."""
    from app.tools import REGISTRY

    routed = {tool for *_, tool, _ in ROUTES}
    assert set(REGISTRY) - routed == {"download_media"}


def test_an_unknown_admin_action_is_a_404(api: tuple[TestClient, list]) -> None:
    client, calls = api
    response = client.post("/api/chats/@g/admin/explode", json={"user": "@u"})
    assert response.status_code == 404
    assert "unknown admin action" in response.json()["detail"]
    assert calls == []


@pytest.mark.parametrize(
    ("raw", "detail"),
    [
        (b"not json", "invalid JSON"),
        (b"[1, 2]", "request body must be a JSON object"),
    ],
)
def test_a_body_that_is_not_a_json_object_is_a_400(
    api: tuple[TestClient, list], raw: bytes, detail: str
) -> None:
    client, calls = api
    response = client.post(
        "/api/messages/forward", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert calls == []


def test_healthz_answers_without_a_tool(api: tuple[TestClient, list]) -> None:
    client, calls = api
    assert client.get("/healthz").json()["status"] == "ok"
    assert calls == []


@pytest.mark.parametrize(
    ("mime_type", "served"),
    [
        ("image/png", "image/png"),
        # The sender controls this value: anything that is not a plain
        # type/subtype is served as an opaque download.
        ("text/html; charset=\u0444", "application/octet-stream"),
        ("not a mime type", "application/octet-stream"),
    ],
)
def test_media_download_streams_the_bytes_with_their_type(
    api: tuple[TestClient, list], monkeypatch: pytest.MonkeyPatch, mime_type: str, served: str
) -> None:
    import app.server as server_mod

    seen: dict[str, Any] = {}

    async def fake_download(holder: Any, chat: str, message_id: int, max_bytes: int) -> Any:
        seen.update(chat=chat, message_id=message_id, max_bytes=max_bytes)
        return {
            "data": b"\x89PNG",
            "mime_type": mime_type,
            "filename": "a.png",
            "media_type": "MessageMediaPhoto",
            "size": 4,
        }

    monkeypatch.setattr(server_mod, "download_media_bytes", fake_download)
    client, _ = api
    response = client.get("/api/messages/7/media", params={"chat": "me", "max_bytes": "100"})
    assert response.status_code == 200
    assert response.content == b"\x89PNG"
    assert response.headers["content-type"].split(";")[0] == served
    assert response.headers["x-content-type-options"] == "nosniff"
    assert seen == {"chat": "me", "message_id": 7, "max_bytes": 100}


@pytest.mark.parametrize(
    ("query", "detail"),
    [
        ({}, "`chat` query param required"),
        ({"chat": "me", "max_bytes": "lots"}, "invalid max_bytes"),
    ],
)
def test_media_download_refuses_bad_queries(
    api: tuple[TestClient, list], query: dict[str, str], detail: str
) -> None:
    client, _ = api
    response = client.get("/api/messages/7/media", params=query)
    assert response.status_code == 400
    assert detail in response.json()["detail"]
