"""Envelope oneof 전 케이스의 직렬화 왕복과 카탈로그 일치를 검사한다.

스크립트(`--gen-dir DIR`) 또는 pytest(환경변수 COZY_GEN_DIR)로 실행한다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 카탈로그: oneof 필드명 -> 필드 번호. 새 메시지를 추가하면 여기도 갱신해야 한다.
CATALOG: dict[str, int] = {
    "client_hello": 10, "server_hello": 11, "error": 12, "ping": 13, "pong": 14,
    "utterance_start": 20, "utterance_end": 21, "utterance_cancel": 22,
    "audio_chunk": 23, "transcript": 24, "response_text": 25,
    "response_audio_start": 26, "response_audio_end": 27, "assistant_state": 28,
    "text_query": 29,
    "alarm_upsert": 40, "alarm_delete": 41, "alarm_ack": 42,
    "alarm_snapshot": 43, "alarm_sync_request": 44,
    "home_snapshot": 60, "entity_state_changed": 61, "entity_command": 62,
    "command_result": 63, "hub_status": 64,
}


def _load(gen_dir: Path):
    """생성 모듈을 임포트한다."""
    sys.path.insert(0, str(gen_dir))
    from cozy.v1 import (alarm_pb2, common_pb2, envelope_pb2, home_pb2,
                         session_pb2, voice_pb2)
    return alarm_pb2, common_pb2, envelope_pb2, home_pb2, session_pb2, voice_pb2


def _gen_dir_from_env() -> Path:
    value = os.environ.get("COZY_GEN_DIR")
    if not value:
        raise RuntimeError("COZY_GEN_DIR is not set; pass --gen-dir when running as a script")
    return Path(value)


def build_bodies(gen_dir: Path) -> dict:
    """oneof 케이스별로 값이 채워진 메시지를 만든다."""
    alarm, common, _env, home, session, voice = _load(gen_dir)
    fmt = voice.AudioFormat(encoding=voice.AUDIO_ENCODING_PCM_S16LE,
                            sample_rate_hz=16000, channels=1)
    state = home.EntityState(state="on", attributes={"brightness": "128"},
                             changed_epoch_ms=1_700_000_000_000)
    alarm_msg = alarm.Alarm(
        alarm_id="a1", kind=alarm.ALARM_KIND_ALARM, label="wake", enabled=True,
        sunrise=True, revision=3, hub_id="hub-1",
        repeating=alarm.Repeating(hour=7, minute=30,
                                  days=[alarm.DAY_OF_WEEK_MONDAY, alarm.DAY_OF_WEEK_SUNDAY]))
    return {
        "client_hello": session.ClientHello(kind=session.CLIENT_KIND_HUB, client_id="hub-1",
                                            token="t", app_version="0.1.0", protocol_version=1),
        "server_hello": session.ServerHello(server_version="0.1.0",
                                            server_time_epoch_ms=1_700_000_000_000,
                                            hub_config=session.HubConfig(name="living", area_id="ar1")),
        "error": session.ErrorMessage(code=common.ERROR_CODE_BAD_REQUEST, message_key="err.bad",
                                      request_id="r1", stream_id="s1"),
        "ping": session.Ping(sent_epoch_ms=1_700_000_000_001),
        "pong": session.Pong(ping_sent_epoch_ms=1_700_000_000_001),
        "utterance_start": voice.UtteranceStart(stream_id="s1", format=fmt, area_id="ar1",
                                                source=voice.WAKE_SOURCE_WAKE_WORD, pre_roll_ms=300),
        "utterance_end": voice.UtteranceEnd(stream_id="s1"),
        "utterance_cancel": voice.UtteranceCancel(stream_id="s1", reason=voice.CANCEL_REASON_BARGE_IN),
        "audio_chunk": voice.AudioChunk(kind=voice.STREAM_KIND_UTTERANCE_UP, stream_id="s1",
                                        index=5, data=b"\x00\x01\xff" * 100, last=True),
        "transcript": voice.Transcript(stream_id="s1", text="hello", final=True),
        "response_text": voice.ResponseText(stream_id="s1", delta="hi", final=True),
        "response_audio_start": voice.ResponseAudioStart(
            stream_id="s2", kind=voice.STREAM_KIND_RESPONSE_DOWN, format=fmt),
        "response_audio_end": voice.ResponseAudioEnd(stream_id="s2"),
        "assistant_state": voice.AssistantState(state=voice.AssistantState.STATE_SPEAKING,
                                                stream_id="s2"),
        "text_query": voice.TextQuery(request_id="r1", text="lights", area_id="ar1"),
        "alarm_upsert": alarm.AlarmUpsert(request_id="r1", alarm=alarm_msg),
        "alarm_delete": alarm.AlarmDelete(request_id="r2", alarm_id="a1", hub_id="hub-1"),
        "alarm_ack": alarm.AlarmAck(request_id="r1", alarm_id="a1", revision=4, ok=False,
                                    code=common.ERROR_CODE_NOT_FOUND),
        "alarm_snapshot": alarm.AlarmSnapshot(hub_id="hub-1", alarms=[
            alarm_msg,
            alarm.Alarm(alarm_id="t1", kind=alarm.ALARM_KIND_TIMER, enabled=True, revision=1,
                        hub_id="hub-1",
                        one_shot=alarm.OneShot(fires_at_epoch_ms=1_700_000_600_000))]),
        "alarm_sync_request": alarm.AlarmSyncRequest(hub_id="hub-1"),
        "home_snapshot": home.HomeSnapshot(
            areas=[home.Area(area_id="ar1", name="Living", aliases=["안방"])],
            entities=[home.Entity(entity_id="light.a", area_id="ar1", name="Lamp",
                                  domain="light", state=state,
                                  aliases=["거실 조명"])]),
        "entity_state_changed": home.EntityStateChanged(entity_id="light.a", state=state),
        "entity_command": home.EntityCommand(request_id="r3", entity_id="light.a",
                                             action="turn_on", params={"brightness": "200"}),
        "command_result": home.CommandResult(request_id="r3", ok=False,
                                             code=common.ERROR_CODE_UPSTREAM_UNAVAILABLE,
                                             message_key="err.upstream"),
        "hub_status": home.HubStatus(volume_pct=40, mic_muted=True, screen_brightness_pct=70),
    }


def check_catalog(gen_dir: Path) -> None:
    """oneof 필드 집합과 번호가 카탈로그와 같은지 검사한다."""
    envelope_pb2 = _load(gen_dir)[2]
    oneof = envelope_pb2.Envelope.DESCRIPTOR.oneofs_by_name["body"]
    actual = {f.name: f.number for f in oneof.fields}
    assert actual == CATALOG, f"oneof mismatch: {set(actual.items()) ^ set(CATALOG.items())}"


def check_roundtrip(gen_dir: Path) -> None:
    """모든 oneof 케이스를 직렬화·파싱해 동일한지 검사한다."""
    envelope_pb2 = _load(gen_dir)[2]
    bodies = build_bodies(gen_dir)
    assert set(bodies) == set(CATALOG), f"missing test bodies: {set(CATALOG) ^ set(bodies)}"
    for seq, (name, body) in enumerate(bodies.items(), start=1):
        original = envelope_pb2.Envelope(seq=seq)
        getattr(original, name).CopyFrom(body)
        parsed = envelope_pb2.Envelope()
        parsed.ParseFromString(original.SerializeToString())
        assert parsed == original, f"roundtrip mismatch: {name}"
        assert parsed.WhichOneof("body") == name, f"wrong oneof case: {name}"
        assert parsed.seq == seq


def test_catalog() -> None:
    check_catalog(_gen_dir_from_env())


def test_roundtrip() -> None:
    check_roundtrip(_gen_dir_from_env())


def main() -> int:
    """스크립트 진입점. 실패 시 비정상 종료 코드를 돌려준다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen-dir", type=Path, required=True)
    args = parser.parse_args()
    check_catalog(args.gen_dir)
    check_roundtrip(args.gen_dir)
    sys.stdout.write(f"roundtrip ok: {len(CATALOG)} cases\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
