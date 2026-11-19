"""ClarifyStore: 허브를 모르면 보류 질문을 기억하지 않는다."""

from dataclasses import replace

from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.model import Clarify, ClarifyReason
from tests.agent_skill_support import make_ctx

QUESTION = Clarify(ClarifyReason.AMBIGUOUS_TARGET, ("light.a", "light.b"))


def test_empty_hub_id_is_never_shared() -> None:
    store = ClarifyStore(30.0)
    store.remember("", QUESTION)
    assert store.take("") is None
    assert store.take("hub-1") is None


def test_remember_and_take_per_hub() -> None:
    store = ClarifyStore(30.0)
    store.remember("hub-1", QUESTION)
    assert store.take("hub-2") is None
    assert store.take("hub-1") == QUESTION
    assert store.take("hub-1") is None


def test_conversation_key_falls_back_to_client_when_hub_unknown() -> None:
    hub_ctx = make_ctx("hub-1")
    assert hub_ctx.conversation_key == "hub-1"
    anonymous = replace(make_ctx(""), client_id="phone-1")
    assert anonymous.conversation_key == "client:phone-1"
    assert make_ctx("").conversation_key == ""
