"""REST API surface."""

from __future__ import annotations

import httpx


def test_unknown_route(http: httpx.Client) -> None:
    resp = http.get("/api/nonexistent")
    assert resp.status_code == 404


def test_validation_error(http: httpx.Client) -> None:
    resp = http.post("/api/messages", json={"chat": "me"})
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_extra_fields_rejected(http: httpx.Client) -> None:
    resp = http.post("/api/messages", json={"chat": "me", "text": "hi", "surprise": "field"})
    assert resp.status_code == 400


def test_invalid_json_body(http: httpx.Client) -> None:
    resp = http.post(
        "/api/messages",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_get_me(http: httpx.Client) -> None:
    resp = http.get("/api/me")
    assert resp.status_code == 200
    me = resp.json()
    assert isinstance(me["id"], int)
    assert me["type"]


def test_send_and_read_roundtrip(http: httpx.Client, env: dict[str, str]) -> None:
    chat = env["TEST_CHAT"]
    marker = f"docker-telethon-plus-test-{httpx.__name__}-roundtrip"

    sent = http.post("/api/messages", json={"chat": chat, "text": marker, "silent": True})
    assert sent.status_code == 200, sent.text
    sent_msg = sent.json()
    msg_id = sent_msg["id"]
    assert sent_msg["text"] == marker
    assert sent_msg["out"] is True

    edit = http.patch(
        f"/api/messages/{msg_id}",
        json={"chat": chat, "text": marker + " (edited)"},
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["text"].endswith("(edited)")

    fetched = http.get("/api/messages", params={"chat": chat, "limit": 10})
    assert fetched.status_code == 200, fetched.text
    msgs = fetched.json()
    assert any(m["id"] == msg_id for m in msgs)

    deleted = http.request("DELETE", "/api/messages", json={"chat": chat, "message_ids": [msg_id]})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["requested"] == 1


def test_get_dialogs(http: httpx.Client) -> None:
    resp = http.get("/api/dialogs", params={"limit": 5})
    assert resp.status_code == 200, resp.text
    dialogs = resp.json()
    assert isinstance(dialogs, list)
    if dialogs:
        assert "id" in dialogs[0]
        assert "type" in dialogs[0]


def test_dialogs_search_param(http: httpx.Client) -> None:
    """Search is now a query param on /api/dialogs, not a separate endpoint."""
    resp = http.get("/api/dialogs", params={"search": "z", "limit": 5})
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_get_entity_self(http: httpx.Client, env: dict[str, str]) -> None:
    resp = http.get("/api/entities", params={"chat": env["TEST_CHAT"]})
    assert resp.status_code == 200, resp.text
    assert "id" in resp.json()


def test_get_entity_unknown_username(http: httpx.Client) -> None:
    resp = http.get(
        "/api/entities",
        params={"chat": "@this_username_should_not_exist_xyzzy_42"},
    )
    assert resp.status_code in (400, 502)


def test_read_public_channel(http: httpx.Client) -> None:
    resp = http.get("/api/messages", params={"chat": "@telegram", "limit": 5})
    assert resp.status_code == 200, resp.text
    msgs = resp.json()
    assert isinstance(msgs, list)
    assert len(msgs) > 0
    msg = msgs[0]
    assert isinstance(msg["id"], int)
    assert msg["chat_id"] is not None


def test_create_and_delete_group(http: httpx.Client) -> None:
    created = http.post("/api/chats", json={"title": "docker-telethon-plus-test-group"})
    assert created.status_code == 200, created.text
    group = created.json()
    assert group["title"] == "docker-telethon-plus-test-group"
    assert isinstance(group["id"], int)

    chat_id = str(group["id"])
    deleted = http.request("DELETE", "/api/chats", json={"chat": chat_id})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["ok"] is True


def test_get_participants_of_own_group(http: httpx.Client) -> None:
    created = http.post("/api/chats", json={"title": "docker-telethon-plus-test-participants"})
    assert created.status_code == 200, created.text
    chat_id = str(created.json()["id"])

    try:
        resp = http.get("/api/participants", params={"chat": chat_id, "limit": 10})
        assert resp.status_code == 200, resp.text
        participants = resp.json()
        assert isinstance(participants, list)
        assert len(participants) >= 1
        assert all("id" in p for p in participants)
    finally:
        http.request("DELETE", "/api/chats", json={"chat": chat_id})


def test_throttle_status_route(http: httpx.Client) -> None:
    resp = http.get("/api/throttle/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "throttle" in body
    assert "buckets" in body["throttle"]


def test_account_health_route(http: httpx.Client) -> None:
    resp = http.get("/api/account/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "risk" in body
    assert body["risk"] in ("ok", "warning", "high")


def test_throttle_headers_present(http: httpx.Client) -> None:
    resp = http.get("/api/me")
    assert "X-Throttle-Multiplier" in resp.headers
    assert "X-RateLimit-Remaining-read" in resp.headers


