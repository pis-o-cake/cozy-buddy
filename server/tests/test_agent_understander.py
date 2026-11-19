"""Understander 시도 순서와 맥락 해석(확인 질문 답) 테스트."""

from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.context import TurnContext
from app.domain.agent.model import (
    CancelAllAlarms,
    CancelSelected,
    Clarify,
    ClarifyReason,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Dismiss,
    Intent,
    TimerIntent,
    WeatherIntent,
)
from app.domain.agent.skills.alarm_select import AlarmSelector
from app.domain.agent.understand.context import ClarifyAnswerResolver, ContextAttempt
from app.domain.agent.understand.steps import MatcherAttempt, WeatherAttempt
from app.domain.agent.understand.understander import Step, Understander
from app.domain.weather.schemas import WeatherWhen
from tests.agent_skill_support import HUB, TZ, FakeAlarmStore, alarm_row, make_ctx

TWO_LIGHTS = ("light.living_ceiling", "light.living_stand")


class Recorder:
    """호출 순서를 기록하고 정해 둔 의도를 돌려주는 시도."""

    def __init__(self, step: Step, result: Intent | None, log: list[Step]) -> None:
        self.step = step
        self._result = result
        self._log = log

    async def __call__(self, text: str, ctx: TurnContext) -> Intent | None:
        self._log.append(self.step)
        return self._result


async def test_understander_stops_at_first_attempt_that_yields_an_intent() -> None:
    log: list[Step] = []
    weather = WeatherIntent(WeatherWhen.NOW)
    attempts = [
        Recorder(Step.CONTEXT, None, log),
        Recorder(Step.MATCHER, TimerIntent(60), log),
        Recorder(Step.WEATHER, weather, log),
    ]

    result = await Understander(attempts).understand("x", make_ctx())

    assert (result.step, result.intent) == (Step.MATCHER, TimerIntent(60))
    assert log == [Step.CONTEXT, Step.MATCHER]


async def test_understander_falls_through_to_llm_when_nothing_matches() -> None:
    log: list[Step] = []
    attempts = [Recorder(Step.CONTEXT, None, log), Recorder(Step.MATCHER, None, log)]

    result = await Understander(attempts).understand("x", make_ctx())

    assert (result.step, result.intent) == (Step.LLM, None)
    assert log == [Step.CONTEXT, Step.MATCHER]


def real_understander(store: ClarifyStore, alarms: FakeAlarmStore) -> Understander:
    resolver = ClarifyAnswerResolver(store, alarms, AlarmSelector(alarms, TZ))
    return Understander([ContextAttempt([resolver]), MatcherAttempt(), WeatherAttempt()])


async def test_real_order_context_beats_matcher_and_weather_is_separate() -> None:
    store = ClarifyStore(30.0)
    understander = real_understander(store, FakeAlarmStore())
    ctx = make_ctx()

    plain = await understander.understand("거실 조명 꺼줘", ctx)
    weather = await understander.understand("오늘 날씨 어때", ctx)
    other = await understander.understand("농담 하나 해줘", ctx)

    assert plain.step is Step.MATCHER and isinstance(plain.intent, DeviceCommand)
    assert weather.step is Step.WEATHER and isinstance(weather.intent, WeatherIntent)
    assert other.step is Step.LLM and other.intent is None


async def test_pending_clarify_answer_is_context_step() -> None:
    store = ClarifyStore(30.0)
    store.remember(
        HUB,
        Clarify(
            ClarifyReason.AMBIGUOUS_TARGET,
            TWO_LIGHTS,
            action=DeviceAction.TURN_OFF,
            params=CommandParams(),
        ),
    )
    understander = real_understander(store, FakeAlarmStore())

    result = await understander.understand("전부 다", make_ctx())

    assert result.step is Step.CONTEXT
    assert result.intent == DeviceCommand(TWO_LIGHTS, DeviceAction.TURN_OFF, CommandParams())


async def test_unrelated_utterance_consumes_pending_and_goes_to_matcher() -> None:
    store = ClarifyStore(30.0)
    store.remember(HUB, Clarify(ClarifyReason.BROAD_ACTION, TWO_LIGHTS, DeviceAction.TURN_OFF))
    understander = real_understander(store, FakeAlarmStore())

    result = await understander.understand("타이머 5분", make_ctx())

    assert result.step is Step.MATCHER and isinstance(result.intent, TimerIntent)
    assert store.take(HUB) is None


async def test_clarify_no_is_dismiss_and_broad_yes_confirms() -> None:
    store = ClarifyStore(30.0)
    understander = real_understander(store, FakeAlarmStore())
    broad = Clarify(ClarifyReason.BROAD_ACTION, TWO_LIGHTS, DeviceAction.TURN_OFF)

    store.remember(HUB, broad)
    no = await understander.understand("아니", make_ctx())
    store.remember(HUB, broad)
    yes = await understander.understand("응", make_ctx())
    store.remember(HUB, Clarify(ClarifyReason.BROAD_ACTION))
    cancel_all = await understander.understand("응", make_ctx())

    assert no.intent == Dismiss()
    assert isinstance(yes.intent, DeviceCommand) and yes.intent.entity_ids == TWO_LIGHTS
    assert cancel_all.intent == CancelAllAlarms()


async def test_ambiguous_target_narrows_by_area_name() -> None:
    store = ClarifyStore(30.0)
    ids = ("light.living_ceiling", "light.bedroom_ceiling")
    store.remember(
        HUB, Clarify(ClarifyReason.AMBIGUOUS_TARGET, ids, DeviceAction.TURN_ON, CommandParams())
    )
    understander = real_understander(store, FakeAlarmStore())

    result = await understander.understand("침실", make_ctx())

    assert result.intent == DeviceCommand(
        ("light.bedroom_ceiling",), DeviceAction.TURN_ON, CommandParams()
    )


async def test_ambiguous_alarm_answer_by_ordinal_and_all() -> None:
    store = ClarifyStore(30.0)
    alarms = FakeAlarmStore()
    alarms.rows = [alarm_row("a1", hour=7), alarm_row("a2", hour=8)]
    understander = real_understander(store, alarms)
    pending = Clarify(ClarifyReason.AMBIGUOUS_ALARM, candidate_alarm_ids=("a1", "a2"))

    store.remember(HUB, pending)
    second = await understander.understand("두 번째", make_ctx())
    store.remember(HUB, pending)
    both = await understander.understand("둘 다", make_ctx())

    assert second.intent == CancelSelected(("a2",), confirmed=False)
    assert both.intent == CancelSelected(("a1", "a2"), confirmed=True)


async def test_ambiguous_alarm_answer_with_vanished_rows() -> None:
    store = ClarifyStore(30.0)
    understander = real_understander(store, FakeAlarmStore())
    store.remember(HUB, Clarify(ClarifyReason.AMBIGUOUS_ALARM, candidate_alarm_ids=("gone",)))

    result = await understander.understand("첫 번째", make_ctx())

    assert result.intent == CancelSelected(())
