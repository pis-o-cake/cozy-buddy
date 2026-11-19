"""AlarmSelector 라벨 매칭."""

import pytest

from app.domain.agent.model import CancelAlarmIntent
from app.domain.agent.skills.alarm_select import AlarmSelector
from tests.agent_fixtures import KST, NOW
from tests.agent_skill_support import FakeAlarmStore, alarm_row


@pytest.fixture
def selector() -> AlarmSelector:
    return AlarmSelector(FakeAlarmStore(), KST)


@pytest.mark.parametrize("label", ["다음", "운동", "아침 운동"])
def test_labeled_cancel_skips_rows_without_label(selector: AlarmSelector, label: str) -> None:
    unlabeled = alarm_row("a1", label="")
    assert not selector.matches(unlabeled, CancelAlarmIntent(label=label), NOW)


def test_labeled_cancel_matches_containing_label(selector: AlarmSelector) -> None:
    row = alarm_row("a1", label="아침 운동")
    assert selector.matches(row, CancelAlarmIntent(label="운동"), NOW)
    assert selector.matches(alarm_row("a2", label="운동"), CancelAlarmIntent(label="아침운동"), NOW)
    assert not selector.matches(row, CancelAlarmIntent(label="약"), NOW)


def test_unlabeled_cancel_matches_any_row(selector: AlarmSelector) -> None:
    assert selector.matches(alarm_row("a1", label=""), CancelAlarmIntent(), NOW)
