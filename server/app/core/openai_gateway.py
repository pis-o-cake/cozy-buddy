"""OpenAI 호출의 단일 관문. STT, LLM, TTS 가 모두 여기를 거친다.

공급자 교체나 테스트용 가짜는 이 클래스 하나만 바꾸면 된다(ADR 0003).
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import openai
from loguru import logger
from openai import AsyncOpenAI

from app.config import Settings

TTS_SAMPLE_RATE_HZ = 24000
_TTS_READ_BYTES = 4096


class OpenAIGatewayError(Exception):
    """OpenAI 호출 실패(네트워크, 인증, 한도, 형식 오류). 내부용이라 영어 메시지를 쓴다."""


class OpenAIGatewayTimeout(OpenAIGatewayError):
    """STT 전체 제한 시간을 넘겼다."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """LLM 이 요청한 툴 호출 하나. arguments 는 JSON 원문이다."""

    id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> dict[str, Any]:
        """JSON 원문을 dict 로 푼다. 깨졌거나 객체가 아니면 빈 dict."""
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class ChatChunk:
    """LLM 스트림 조각. 텍스트 조각이거나, 턴 끝에 한 번 오는 툴 호출 묶음이다."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class OpenAIGateway:
    """STT, 채팅(툴 호출), TTS 를 제공하는 OpenAI 클라이언트 래퍼."""

    def __init__(self, settings: Settings) -> None:
        self._stt_model = settings.openai_stt_model
        self._llm_model = settings.openai_llm_model
        self._tts_model = settings.openai_tts_model
        self._tts_voice = settings.openai_tts_voice
        self._reasoning_effort = settings.openai_llm_reasoning_effort.strip()
        self._language = settings.locale
        self._stt_hedge_after = settings.stt_hedge_after_ms / 1000
        self._stt_timeout = settings.stt_timeout_ms / 1000
        # 재시도는 지연을 숨기므로 끈다. 실패 정책은 호출하는 쪽이 정한다.
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(), max_retries=0, timeout=30.0
        )

    async def aclose(self) -> None:
        """HTTP 클라이언트를 닫는다."""
        await self._client.close()

    async def transcribe(
        self,
        wav: bytes,
        prompt: str | None = None,
        on_hedge: Callable[[], None] | None = None,
    ) -> str:
        """WAV 음성을 한국어 텍스트로 바꾼다.

        첫 요청이 `stt_hedge_after_ms` 안에 끝나지 않으면 같은 요청을 한 번 더 보내(헤지)
        먼저 끝난 결과를 쓰고 나머지는 취소한다. 헤지는 요청을 두 번 과금하므로 보낼 때마다
        INFO 로그를 남기고 `on_hedge` 를 부른다. 연결 오류·5xx 는 각 요청이 즉시 한 번 재시도한다.

        Args:
            wav: 메모리 상의 WAV 파일 바이트.
            prompt: 방·기기 이름 같은 고유명사 힌트. 없으면 보내지 않는다.
            on_hedge: 헤지 요청을 보낼 때 호출된다.

        Raises:
            OpenAIGatewayTimeout: `stt_timeout_ms` 안에 결과를 받지 못했을 때.
            OpenAIGatewayError: 호출이 실패했을 때.
        """
        try:
            async with asyncio.timeout(self._stt_timeout):
                return await self._transcribe_hedged(wav, prompt, on_hedge)
        except TimeoutError as exc:
            raise OpenAIGatewayTimeout("STT deadline exceeded") from exc

    async def _transcribe_hedged(
        self, wav: bytes, prompt: str | None, on_hedge: Callable[[], None] | None
    ) -> str:
        pending: set[asyncio.Task[str]] = {
            asyncio.ensure_future(self._transcribe_attempt(wav, prompt))
        }
        can_hedge = self._stt_hedge_after > 0
        last_error: OpenAIGatewayError | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=self._stt_hedge_after if can_hedge else None,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    can_hedge = False
                    logger.info("STT hedge fired after {}ms", round(self._stt_hedge_after * 1000))
                    if on_hedge is not None:
                        on_hedge()
                    pending.add(asyncio.ensure_future(self._transcribe_attempt(wav, prompt)))
                    continue
                for task in done:
                    error = task.exception()
                    if error is None:
                        return task.result()
                    if not isinstance(error, OpenAIGatewayError):
                        raise error
                    last_error = error
            raise last_error or OpenAIGatewayError("STT request failed")
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _transcribe_attempt(self, wav: bytes, prompt: str | None) -> str:
        """요청 한 건. 연결 오류·5xx 면 즉시 한 번 더 시도한다."""
        for attempt in (1, 2):
            try:
                return await self._create_transcription(wav, prompt)
            except (openai.APIConnectionError, openai.InternalServerError) as exc:
                if attempt == 1:
                    logger.warning("STT retry after {}", type(exc).__name__)
                    continue
                raise OpenAIGatewayError(f"STT request failed: {type(exc).__name__}") from exc
            except openai.OpenAIError as exc:
                raise OpenAIGatewayError(f"STT request failed: {type(exc).__name__}") from exc
        raise AssertionError("unreachable")

    async def _create_transcription(self, wav: bytes, prompt: str | None) -> str:
        kwargs: dict[str, Any] = {"language": self._language}
        if prompt:
            kwargs["prompt"] = prompt
        result = await self._client.audio.transcriptions.create(
            model=self._stt_model, file=("utterance.wav", wav, "audio/wav"), **kwargs
        )
        return str(result.text)

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """채팅 응답을 스트리밍한다. 텍스트 조각은 즉시, 툴 호출은 턴이 끝날 때 한 번에 낸다.

        Raises:
            OpenAIGatewayError: 호출이 실패했을 때.
        """
        kwargs: dict[str, Any] = {"messages": list(messages), "stream": True}
        if tools:
            kwargs["tools"] = list(tools)
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        slots: dict[int, dict[str, str]] = {}
        try:
            stream = await self._client.chat.completions.create(model=self._llm_model, **kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                for call in delta.tool_calls or []:
                    slot = slots.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] += call.id or ""
                    if call.function:
                        slot["name"] += call.function.name or ""
                        slot["arguments"] += call.function.arguments or ""
                if delta.content:
                    yield ChatChunk(text=delta.content)
        except openai.OpenAIError as exc:
            raise OpenAIGatewayError(f"Chat request failed: {type(exc).__name__}") from exc
        if not slots:
            return
        calls = tuple(
            ToolCall(s["id"], s["name"], s["arguments"]) for _, s in sorted(slots.items())
        )
        logger.debug("LLM requested tools: {}", [c.name for c in calls])
        yield ChatChunk(tool_calls=calls)

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        """텍스트를 PCM_S16LE 24kHz mono 로 합성해 조각으로 낸다.

        Raises:
            OpenAIGatewayError: 호출이 실패했을 때.
        """
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model=self._tts_model,
                voice=self._tts_voice,
                input=text,
                response_format="pcm",
            ) as response:
                async for data in response.iter_bytes(_TTS_READ_BYTES):
                    yield data
        except openai.OpenAIError as exc:
            raise OpenAIGatewayError(f"TTS request failed: {type(exc).__name__}") from exc
