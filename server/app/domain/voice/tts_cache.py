"""합성한 TTS 음성(PCM) 캐시. 메모리 LRU 와 디스크 LRU 두 겹으로, 재시작 뒤에도 남는다.

키는 (TTS 정규화 문장, 모델, 목소리, 형식·샘플레이트)의 해시다. 모델이나 목소리가 바뀌면
키가 달라져 옛 음성이 재사용되지 않는다.
"""

import asyncio
import contextlib
import hashlib
import os
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

_SUFFIX = ".pcm"
_TMP_SUFFIX = ".tmp"


@dataclass(frozen=True, slots=True)
class TtsCacheLimits:
    """캐시 상한(바이트). disk_bytes 가 0 이면 디스크를 쓰지 않는다."""

    memory_bytes: int
    disk_bytes: int
    # 한 문장이 이보다 크면 저장하지 않는다. 비정상적으로 긴 응답이 캐시를 밀어내는 것을 막는다.
    max_entry_bytes: int = 4 * 1024 * 1024


class TtsCache:
    """문장별 PCM 캐시. 이벤트 루프 하나에서만 쓴다.

    디스크 입출력은 스레드로 보내 루프를 막지 않는다. 디스크 쓰기는 임시 파일에 쓴 뒤 rename 으로
    바꿔 읽는 쪽이 반쯤 쓴 파일을 보지 않게 한다. 쓰기는 백그라운드로 돌려 응답을 지연시키지 않는다.
    """

    def __init__(self, directory: Path | None, identity: str, limits: TtsCacheLimits) -> None:
        self._dir = directory
        self._identity = identity
        self._limits = limits
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._memory_total = 0
        self._disk: OrderedDict[str, int] = OrderedDict()
        self._disk_total = 0
        self._writes: set[asyncio.Task[None]] = set()
        self._active_turns = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self.hits = 0
        self.misses = 0
        if directory is not None and limits.disk_bytes > 0:
            self._load_index(directory)
        else:
            self._dir = None

    # --- 조회·저장 ---------------------------------------------------------------------------

    def key(self, text: str) -> str:
        """정규화한 문장의 캐시 키."""
        return hashlib.sha256(f"{self._identity}\0{text}".encode()).hexdigest()

    def has(self, text: str) -> bool:
        """디스크 입출력 없이 캐시에 있는지 본다. 웜업이 이미 있는 문장을 건너뛰는 데 쓴다."""
        key = self.key(text)
        return key in self._memory or key in self._disk

    async def get(self, text: str) -> bytes | None:
        """캐시된 PCM. 없으면 None. 적중하면 최근 사용으로 갱신하고 메모리로 올린다."""
        key = self.key(text)
        pcm = self._memory.get(key)
        if pcm is not None:
            self._memory.move_to_end(key)
            if key in self._disk:
                self._disk.move_to_end(key)
            return pcm
        if key not in self._disk:
            return None
        pcm = await asyncio.to_thread(self._read, key)
        if pcm is None:
            self._forget_disk(key)
            return None
        if key in self._disk:
            self._disk.move_to_end(key)
        self._remember(key, pcm)
        return pcm

    def record(self, *, hit: bool) -> None:
        """조회 결과를 적중률 통계에 센다."""
        if hit:
            self.hits += 1
        else:
            self.misses += 1

    def put(self, text: str, pcm: bytes) -> None:
        """완성된 PCM 을 저장한다. 메모리는 즉시, 디스크는 백그라운드로 쓴다."""
        if not pcm or len(pcm) > self._limits.max_entry_bytes:
            return
        key = self.key(text)
        self._remember(key, pcm)
        if self._dir is None or len(pcm) > self._limits.disk_bytes:
            return
        task = asyncio.create_task(self._persist(key, pcm), name="tts-cache-write")
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def drain(self) -> None:
        """예약된 디스크 쓰기가 끝날 때까지 기다린다."""
        pending = list(self._writes)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    @property
    def memory_bytes(self) -> int:
        """메모리에 올라온 PCM 총 바이트."""
        return self._memory_total

    @property
    def disk_bytes(self) -> int:
        """디스크에 저장된 PCM 총 바이트."""
        return self._disk_total

    @property
    def disk_entries(self) -> int:
        """디스크에 저장된 문장 수."""
        return len(self._disk)

    # --- 사용자 턴 우선 ----------------------------------------------------------------------

    @contextlib.contextmanager
    def turn_active(self) -> Iterator[None]:
        """사용자 턴이 진행 중임을 표시한다. 웜업은 모든 턴이 끝날 때까지 합성을 미룬다."""
        self._active_turns += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._active_turns -= 1
        if self._active_turns == 0:
            self._idle.set()

    @property
    def busy(self) -> bool:
        """사용자 턴이 진행 중인지."""
        return self._active_turns > 0

    async def wait_idle(self) -> None:
        """진행 중인 사용자 턴이 모두 끝날 때까지 기다린다."""
        await self._idle.wait()

    # --- 내부 --------------------------------------------------------------------------------

    def _remember(self, key: str, pcm: bytes) -> None:
        old = self._memory.pop(key, None)
        if old is not None:
            self._memory_total -= len(old)
        if len(pcm) > self._limits.memory_bytes:
            return
        self._memory[key] = pcm
        self._memory_total += len(pcm)
        while self._memory_total > self._limits.memory_bytes:
            _, evicted = self._memory.popitem(last=False)
            self._memory_total -= len(evicted)

    def _path(self, key: str) -> Path:
        assert self._dir is not None
        return self._dir / f"{key}{_SUFFIX}"

    def _load_index(self, directory: Path) -> None:
        """기존 파일로 디스크 색인을 만든다. 최근 사용(mtime) 순으로 쌓고, 임시 파일은 지운다."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            found: list[tuple[float, str, int]] = []
            for entry in os.scandir(directory):
                if entry.name.endswith(_TMP_SUFFIX):
                    with contextlib.suppress(OSError):
                        os.unlink(entry.path)
                elif entry.name.endswith(_SUFFIX):
                    stat = entry.stat()
                    found.append((stat.st_mtime, entry.name[: -len(_SUFFIX)], stat.st_size))
        except OSError as exc:
            logger.warning("TTS disk cache disabled: {}", type(exc).__name__)
            self._dir = None
            return
        for _, key, size in sorted(found):
            self._disk[key] = size
            self._disk_total += size
        for victim in self._evict_disk():
            with contextlib.suppress(OSError):
                self._path(victim).unlink()
        logger.info("TTS disk cache loaded: entries={} bytes={}", len(self._disk), self._disk_total)

    def _evict_disk(self) -> list[str]:
        victims: list[str] = []
        while self._disk_total > self._limits.disk_bytes and self._disk:
            key, size = self._disk.popitem(last=False)
            self._disk_total -= size
            victims.append(key)
        return victims

    def _forget_disk(self, key: str) -> None:
        size = self._disk.pop(key, None)
        if size is not None:
            self._disk_total -= size

    def _read(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            data = path.read_bytes()
            os.utime(path)
        except OSError:
            return None
        return data

    async def _persist(self, key: str, pcm: bytes) -> None:
        try:
            await asyncio.to_thread(self._write, key, pcm)
        except OSError as exc:
            logger.warning("TTS cache write failed: {}", type(exc).__name__)
            return
        self._forget_disk(key)
        self._disk[key] = len(pcm)
        self._disk_total += len(pcm)
        victims = self._evict_disk()
        if victims:
            await asyncio.to_thread(self._unlink_all, victims)

    def _write(self, key: str, pcm: bytes) -> None:
        assert self._dir is not None
        tmp = self._dir / f"{key}.{uuid.uuid4().hex}{_TMP_SUFFIX}"
        try:
            with open(tmp, "wb") as handle:
                handle.write(pcm)
            os.replace(tmp, self._path(key))
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def _unlink_all(self, keys: list[str]) -> None:
        for key in keys:
            if key in self._disk:
                continue
            with contextlib.suppress(OSError):
                self._path(key).unlink()
