"""Polls in the suite's own group: create, vote, read the results. The poll
messages are deleted afterwards."""

from __future__ import annotations

from typing import Callable

import httpx
from conftest import marker, ok

Cleanup = Callable[[str, int], None]


def _create(http: httpx.Client, cleanup: Cleanup, group: str, **fields: object) -> dict:
    poll = ok(http.post("/api/polls", json={"chat": group, **fields}))
    cleanup(group, poll["id"])
    return poll


def _results(http: httpx.Client, group: str, poll_id: int) -> dict:
    return ok(http.get(f"/api/polls/{poll_id}/results", params={"chat": group}))


def test_a_vote_shows_up_in_the_results(http: httpx.Client, group: str, cleanup: Cleanup) -> None:
    question = marker("poll")
    poll = _create(http, cleanup, group, question=question, options=["red", "green", "blue"])
    assert poll["media_type"] == "MessageMediaPoll"

    assert (
        ok(http.post(f"/api/polls/{poll['id']}/vote", json={"chat": group, "options": [1]}))["ok"]
        is True
    )

    # Right after the vote: get_poll_results asks Telegram for current counts.
    results = _results(http, group, poll["id"])
    assert results["question"] == question
    assert results["total_voters"] == 1
    by_index = {r["option_index"]: r for r in results["results"]}
    assert by_index[1]["voters"] == 1
    assert by_index[1]["chosen"] is True
    assert by_index[0]["voters"] == 0


def test_a_multiple_choice_vote_counts_every_option(
    http: httpx.Client, group: str, cleanup: Cleanup
) -> None:
    poll = _create(
        http,
        cleanup,
        group,
        question=marker("multi"),
        options=["a", "b", "c"],
        multiple_choice=True,
    )

    ok(http.post(f"/api/polls/{poll['id']}/vote", json={"chat": group, "options": [0, 2]}))

    chosen = {
        r["option_index"] for r in _results(http, group, poll["id"])["results"] if r["chosen"]
    }
    assert chosen == {0, 2}


def test_a_quiz_marks_the_correct_answer(http: httpx.Client, group: str, cleanup: Cleanup) -> None:
    poll = _create(
        http,
        cleanup,
        group,
        question=marker("quiz"),
        options=["two", "four"],
        quiz=True,
        correct_option=1,
        solution="Two plus two.",
    )

    ok(http.post(f"/api/polls/{poll['id']}/vote", json={"chat": group, "options": [1]}))

    by_index = {r["option_index"]: r for r in _results(http, group, poll["id"])["results"]}
    assert by_index[1]["correct"] is True
    assert by_index[1]["chosen"] is True


def test_a_vote_for_an_option_the_poll_lacks_is_refused(
    http: httpx.Client, group: str, cleanup: Cleanup
) -> None:
    poll = _create(http, cleanup, group, question=marker("range"), options=["yes", "no"])

    refused = http.post(f"/api/polls/{poll['id']}/vote", json={"chat": group, "options": [5]})
    assert refused.status_code == 400
