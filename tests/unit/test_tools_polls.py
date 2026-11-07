"""create_poll, vote_poll and get_poll_results against Telethon's real request
pipeline (see test_tools_support.make_wire) and real poll messages."""

from __future__ import annotations

import json
from typing import Any, List
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from telethon.extensions import BinaryReader
from telethon.tl.types import (
    Message,
    MessageMediaPoll,
    PeerUser,
    Poll,
    PollAnswer,
    PollAnswerVoters,
    PollResults,
    TextWithEntities,
)

from .test_tools_support import (
    SENT_MESSAGE_ID,
    make_user,
    make_wire,
    sent_message_updates,
)

# Tokens the server would assign; none is bytes([index]).
TOKENS = [b"tok-a", b"tok-b", b"tok-c"]


def _tool(name: str) -> Any:
    from app.tools import REGISTRY

    return REGISTRY[name]


def _params(name: str, **fields: Any) -> Any:
    return _tool(name).params_model.model_validate({"chat": "5", **fields})


def _roundtrip(request: Any) -> Any:
    """Parse the request's bytes back, as the server would read them."""
    return BinaryReader(request._bytes()).tgread_object()


@pytest.fixture
def dry_run_holder(base_env: None, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.client import TelethonHolder
    from app.config import Config

    monkeypatch.setenv("TELETHON_DRY_RUN", "true")
    holder = TelethonHolder(Config.from_env())
    holder._client = AsyncMock()
    holder._client.get_entity = AsyncMock(return_value=make_user())
    return holder


def _poll_message(tokens: List[bytes], results: PollResults | None = None) -> Message:
    poll = Poll(
        id=1,
        hash=0,
        question=TextWithEntities(text="Which one?", entities=[]),
        answers=[
            PollAnswer(text=TextWithEntities(text=f"option {i}", entities=[]), option=token)
            for i, token in enumerate(tokens)
        ],
    )
    media = MessageMediaPoll(poll=poll, results=results or PollResults())
    return Message(id=9, peer_id=PeerUser(5), media=media)


# ---------- create_poll -----------------------------------------------------


async def test_create_poll_sends_a_request_telegram_can_read(holder) -> None:
    """Regression: Poll() lacked its hash and took str where TextWithEntities
    belongs, so every create_poll call failed before reaching Telegram."""
    wire = make_wire(sent_message_updates)
    wire.client.get_entity = AsyncMock(return_value=make_user())
    holder._client = wire.client

    result = await _tool("create_poll").handler(
        holder, _params("create_poll", question="Pick one", options=["red", "green", "blue"])
    )

    assert result["id"] == SENT_MESSAGE_ID
    sent = _roundtrip(wire.sent[0])
    assert sent.media.poll.question.text == "Pick one"
    assert [a.text.text for a in sent.media.poll.answers] == ["red", "green", "blue"]
    assert not sent.media.poll.quiz
    assert not sent.media.poll.multiple_choice
    assert not sent.media.correct_answers


async def test_create_poll_sends_a_multiple_choice_poll(holder) -> None:
    wire = make_wire(sent_message_updates)
    wire.client.get_entity = AsyncMock(return_value=make_user())
    holder._client = wire.client

    await _tool("create_poll").handler(
        holder,
        _params("create_poll", question="Any?", options=["x", "y"], multiple_choice=True),
    )

    assert _roundtrip(wire.sent[0]).media.poll.multiple_choice


async def test_create_poll_sends_a_quiz_with_its_answer_and_explanation(holder) -> None:
    wire = make_wire(sent_message_updates)
    wire.client.get_entity = AsyncMock(return_value=make_user())
    holder._client = wire.client

    await _tool("create_poll").handler(
        holder,
        _params(
            "create_poll",
            question="Which is right?",
            options=["a", "b", "c"],
            quiz=True,
            correct_option=2,
            solution="Because c",
        ),
    )

    sent = _roundtrip(wire.sent[0])
    assert sent.media.poll.quiz
    assert sent.media.correct_answers == [2]
    assert sent.media.solution == "Because c"


async def test_create_poll_sends_a_quiz_without_an_explanation(holder) -> None:
    wire = make_wire(sent_message_updates)
    wire.client.get_entity = AsyncMock(return_value=make_user())
    holder._client = wire.client

    await _tool("create_poll").handler(
        holder,
        _params("create_poll", question="Q?", options=["a", "b"], quiz=True, correct_option=0),
    )

    sent = _roundtrip(wire.sent[0])
    assert sent.media.correct_answers == [0]
    assert not sent.media.solution


@pytest.mark.parametrize("dry_run", [False, True])
async def test_create_poll_quiz_without_correct_option_is_rejected(
    holder, dry_run_holder, dry_run
) -> None:
    target = dry_run_holder if dry_run else holder
    params = _params("create_poll", question="Q?", options=["a", "b"], quiz=True)
    with pytest.raises(ValueError, match="correct_option"):
        await _tool("create_poll").handler(target, params)
    target._client.send_file.assert_not_awaited()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("correct_option", [2, 7])
async def test_create_poll_correct_option_beyond_the_options_is_rejected(
    holder, dry_run_holder, dry_run, correct_option
) -> None:
    target = dry_run_holder if dry_run else holder
    params = _params(
        "create_poll", question="Q?", options=["a", "b"], quiz=True, correct_option=correct_option
    )
    with pytest.raises(ValueError, match="out of range"):
        await _tool("create_poll").handler(target, params)
    target._client.send_file.assert_not_awaited()


def test_create_poll_question_is_capped_at_what_telegram_accepts() -> None:
    _params("create_poll", question="q" * 255, options=["a", "b"])
    with pytest.raises(ValidationError):
        _params("create_poll", question="q" * 256, options=["a", "b"])


def test_create_poll_each_option_is_capped_at_what_telegram_accepts() -> None:
    _params("create_poll", question="Q?", options=["o" * 100, "b"])
    with pytest.raises(ValidationError):
        _params("create_poll", question="Q?", options=["a", "o" * 101])


# ---------- get_poll_results ------------------------------------------------


def _results() -> PollResults:
    return PollResults(
        total_voters=7,
        results=[
            PollAnswerVoters(option=TOKENS[2], voters=4, correct=True, chosen=False),
            PollAnswerVoters(option=TOKENS[0], voters=3, correct=False, chosen=True),
        ],
    )


async def test_get_poll_results_is_json_with_the_question_text_and_real_indices(
    holder, make_entity
) -> None:
    """Regression: the question was a TextWithEntities, which JSON cannot
    encode (a 500 over REST), and option_index was the first byte of the
    server's opaque option token."""
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS, _results())])

    result = await _tool("get_poll_results").handler(
        holder, _params("get_poll_results", message_id=9)
    )

    assert json.loads(json.dumps(result)) == {
        "question": "Which one?",
        "total_voters": 7,
        "results": [
            {"option_index": 2, "voters": 4, "correct": True, "chosen": False},
            {"option_index": 0, "voters": 3, "correct": False, "chosen": True},
        ],
    }


async def test_get_poll_results_leaves_a_token_it_cannot_place_without_an_index(
    holder, make_entity
) -> None:
    results = PollResults(total_voters=1, results=[PollAnswerVoters(option=b"gone", voters=1)])
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS, results)])

    result = await _tool("get_poll_results").handler(
        holder, _params("get_poll_results", message_id=9)
    )

    assert result["results"][0]["option_index"] is None


async def test_get_poll_results_before_any_vote_lists_no_results(holder, make_entity) -> None:
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS)])

    result = await _tool("get_poll_results").handler(
        holder, _params("get_poll_results", message_id=9)
    )

    assert result["results"] == []
    assert result["total_voters"] is None


async def test_get_poll_results_costs_one_read_slot_and_one_request(holder, make_entity) -> None:
    """Regression: an unused GetMessagesReactions call was made on top."""
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS, _results())])
    used_before = holder.throttle.snapshot()["buckets"]["read"]["used"]

    await _tool("get_poll_results").handler(holder, _params("get_poll_results", message_id=9))

    used_after = holder.throttle.snapshot()["buckets"]["read"]["used"]
    assert used_after - used_before == 1
    holder._client.assert_not_awaited()


async def test_get_poll_results_of_a_message_without_a_poll_is_rejected(
    holder, make_entity
) -> None:
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    plain = Message(id=9, peer_id=PeerUser(5), message="no poll here")
    holder._client.get_messages = AsyncMock(return_value=[plain])

    with pytest.raises(ValueError, match="no poll"):
        await _tool("get_poll_results").handler(holder, _params("get_poll_results", message_id=9))


# ---------- vote_poll -------------------------------------------------------


async def test_vote_poll_sends_the_answers_tokens_not_the_indices(holder, make_entity) -> None:
    """Regression: the vote carried bytes([index]); Telegram identifies an
    answer by the opaque token it assigned, so no vote ever matched."""
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS)])
    holder._client.return_value = None

    result = await _tool("vote_poll").handler(
        holder, _params("vote_poll", message_id=9, options=[2, 0])
    )

    assert result == {"ok": True}
    request = holder._client.await_args.args[0]
    assert request.options == [TOKENS[2], TOKENS[0]]
    assert request.msg_id == 9


async def test_vote_poll_request_serialises(holder) -> None:
    wire = make_wire()
    wire.client.get_entity = AsyncMock(return_value=make_user())
    wire.client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS)])
    holder._client = wire.client

    await _tool("vote_poll").handler(holder, _params("vote_poll", message_id=9, options=[1]))

    assert _roundtrip(wire.sent[0]).options == [TOKENS[1]]


@pytest.mark.parametrize("options", [[3], [-1], [0, 3]])
async def test_vote_poll_index_outside_the_poll_is_rejected(holder, make_entity, options) -> None:
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[_poll_message(TOKENS)])

    with pytest.raises(ValueError, match="out of range"):
        await _tool("vote_poll").handler(
            holder, _params("vote_poll", message_id=9, options=options)
        )
    holder._client.assert_not_awaited()


async def test_vote_poll_on_a_message_without_a_poll_is_rejected(holder, make_entity) -> None:
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(
        return_value=[Message(id=9, peer_id=PeerUser(5), message="no poll here")]
    )

    with pytest.raises(ValueError, match="no poll"):
        await _tool("vote_poll").handler(holder, _params("vote_poll", message_id=9, options=[0]))
    holder._client.assert_not_awaited()


async def test_vote_poll_on_a_missing_message_is_rejected(holder, make_entity) -> None:
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[None])

    with pytest.raises(ValueError, match="no poll"):
        await _tool("vote_poll").handler(holder, _params("vote_poll", message_id=9, options=[0]))
