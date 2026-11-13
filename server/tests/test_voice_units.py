"""음성 파이프라인 단위 테스트: TTS 정규화, 문장 분리, PCM, 설정 검증, 로그 마스킹."""

import io
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.logging import mask_secrets
from app.domain.voice.audio import PcmChunker, pcm_to_wav
from app.domain.voice.tts_text import normalize_for_tts, split_sentences


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("거실 조명 밝기를 50%로 맞췄어요", "거실 조명 밝기를 50퍼센트로 맞췄어요"),
        ("오전 7:30에 울려요", "오전 7시 30분에 울려요"),
        ("18:00 알람", "18시 알람"),
        ("지금 온도는 23.5°C예요", "지금 온도는 23.5도예요"),
        ("소비 전력은 120W예요", "소비 전력은 120와트예요"),
        ("**네** 켰어요", "네 켰어요"),
        ("12:345 는 시각이 아니에요", "12:345 는 시각이 아니에요"),
    ],
)
def test_normalize_for_tts(raw: str, expected: str) -> None:
    assert normalize_for_tts(raw) == expected


def test_split_sentences_keeps_unfinished_tail() -> None:
    sentences, tail = split_sentences("네, 켰어요. 그리고 온도는 21.5도예요. 지금", final=False)
    assert sentences == ["네, 켰어요.", "그리고 온도는 21.5도예요."]
    assert tail == "지금"
    assert split_sentences(tail, final=True) == (["지금"], "")


def test_pcm_chunker_keeps_sample_alignment() -> None:
    chunker = PcmChunker(24000, 30)
    chunks = chunker.feed(b"\x00" * 3001)
    assert [len(c) for c in chunks] == [1440, 1440]
    assert len(chunker.flush()) == 120


def test_pcm_to_wav_declares_rate_and_format() -> None:
    wav = pcm_to_wav(b"\x00\x00" * 160, 16000)
    with wave.open(io.BytesIO(wav)) as reader:
        assert (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (
            16000,
            1,
            2,
        )
        assert reader.getnframes() == 160


def test_voice_requires_models_when_api_key_present() -> None:
    with pytest.raises(ValidationError, match="OPENAI_LLM_MODEL"):
        Settings(_env_file=None, openai_api_key="placeholder", openai_stt_model="m")  # type: ignore[call-arg, arg-type]


def test_voice_disabled_without_api_key() -> None:
    assert not Settings(_env_file=None).voice_enabled  # type: ignore[call-arg]


def test_openai_settings_are_read_from_unprefixed_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=placeholder\n"
        "OPENAI_STT_MODEL=stt-x\nOPENAI_LLM_MODEL=llm-x\n"
        "OPENAI_TTS_MODEL=tts-x\nOPENAI_TTS_VOICE=voice-x\n"
        "COZY_STT_PROMPT_ENABLED=false\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert settings.voice_enabled
    assert settings.openai_llm_model == "llm-x"
    assert settings.stt_prompt_enabled is False


def test_mask_secrets_hides_api_keys() -> None:
    # 스캐너 오탐을 피하려고 키 모양 문자열을 런타임에 이어 붙인다.
    fake_key = "sk-" + "proj-abcDEF1234567890_xyz"
    masked = mask_secrets(f"auth failed for {fake_key} and sk-short")
    assert "abcDEF" not in masked
    assert masked == "auth failed for sk-*** and sk-short"
