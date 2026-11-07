"""get_participants charges the get_full bucket for every request Telethon's
participants iterator sends.

The channel cases run the real iterator against a fake wire (see
test_tools_support.make_wire) and compare the charge with the requests that
actually went out, so a change in how Telethon pages shows up here."""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock

import pytest
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipant
from telethon.tl.types.channels import ChannelParticipants

from .test_tools_support import NOW, make_basic_group, make_supergroup, make_user, make_wire

CHAT = "1"


def _members_wire(members: int) -> Any:
    """A channel with `members` members, served page by page."""
    population = [make_user(eid) for eid in range(1, members + 1)]

    def answer(request: Any) -> ChannelParticipants:
        assert isinstance(request, GetParticipantsRequest)
        page = population[request.offset : request.offset + request.limit]
        return ChannelParticipants(
            count=len(population),
            participants=[ChannelParticipant(user_id=u.id, date=NOW) for u in page],
            chats=[],
            users=page,
        )

    return make_wire(answer)


def _used(holder: Any) -> int:
    return holder.throttle.snapshot()["buckets"]["get_full"]["used"]


async def _list(holder: Any, **fields: Any) -> List[Any]:
    from app.tools import REGISTRY

    tool = REGISTRY["get_participants"]
    params = tool.params_model.model_validate({"chat": CHAT, **fields})
    return await tool.handler(holder, params)


@pytest.mark.parametrize(
    "members,limit,search",
    [
        (450, 1000, ""),  # three full pages, then an empty one that ends the list
        (400, 400, ""),  # the limit is met on a page boundary: no empty page
        (100, 100, ""),
        (150, 100, ""),  # more members than the limit: stop at the limit
        (0, 100, ""),  # nobody: the first page is the empty one
        (10, 100, "ann"),  # a search adds a request for the total
        (10, 10, "ann"),
        (450, 500, "ann"),
    ],
)
async def test_every_request_the_participants_iterator_sends_is_charged(
    holder, members, limit, search
) -> None:
    """Regression: only the pages with members were counted, so the empty
    page that ends a short list and the search's total request went free."""
    wire = _members_wire(members)
    wire.client.get_entity = AsyncMock(return_value=make_supergroup())
    holder._client = wire.client
    used_before = _used(holder)

    listed = await _list(holder, limit=limit, search=search or None)

    assert len(listed) == min(members, limit)
    assert _used(holder) - used_before == len(wire.sent)


async def test_a_basic_group_costs_the_one_request_that_lists_it(holder, make_entity) -> None:
    """Telethon reads a basic group from a single GetFullChat request."""
    holder._client.get_entity = AsyncMock(return_value=make_basic_group())
    holder._client.get_participants = AsyncMock(return_value=[make_entity(i) for i in range(30)])
    used_before = _used(holder)

    await _list(holder, limit=100)

    assert _used(holder) - used_before == 1
