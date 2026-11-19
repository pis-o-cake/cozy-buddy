---
type: architecture
status: active
last-reviewed: 2026-11-19
---

# 현행 아키텍처

Cozy Buddy는 방마다 둔 Android 태블릿 허브와 집 안 서버 한 대로 동작한다. 허브는 호출어를 듣고 알람을 울리며,
호출어 뒤의 발화만 서버로 보낸다. 서버는 음성을 클라우드 AI(OpenAI)로 처리하고, 기기 제어는 Home Assistant(HA)에
맡긴다. 허브·앱과 서버 사이 계약은 `protocol/`의 Protocol Buffers 메시지 하나다. 휴대폰 앱은 아직 없다.

## 구조 요약

```
[허브 태블릿] ─ WebSocket(protobuf 바이너리) ─┐
[휴대폰 앱, 미구현] ─ REST + WebSocket ───────┤
                                     nginx ─ cozy-server(FastAPI, 워커 1개, SQLite)
                                                 ├─ HA WebSocket API ─ Home Assistant ─ 기기
                                                 ├─ OpenAI(STT · LLM · TTS)
                                                 └─ Open-Meteo(날씨)
```

- 서버는 프로세스 하나다. WebSocket 연결, HA 구독, 일일 비용 한도, 대화 맥락을 메모리에 두므로 워커를 늘리지 않는다.
- 알람의 정본은 허브다. 서버는 앱 표시와 원격 설정을 위한 사본만 갖고, 허브가 보낸 스냅샷이 이긴다.
- 호출어 감지와 발화 끝 판정(VAD)은 허브에서 한다. 호출어 전의 소리는 허브 밖으로 나가지 않는다.

## 구성요소와 책임

### 서버 (`server/app/`)

도메인 폴더 하나가 그 도메인의 `api`·`service`·`crud`·`models`·`schemas` 가운데 필요한 것을 갖는다. `main.py`가
`domain/*/api.py`를 자동 등록한다.

| 도메인 | 책임 | 저장 |
|---|---|---|
| `gateway` | `/ws` 연결 수락, `ClientHello` 인증, Envelope를 도메인으로 전달 | 없음 |
| `auth` | 정적 토큰과 등록 코드로 발급한 클라이언트 토큰, 관리 CLI | `client_token`(해시만) |
| `voice` | 발화 세션, STT 호출, 응답 문장의 TTS 스트리밍, 다중 허브 중재(`arbiter`), TTS 캐시와 미리 합성 | TTS 디스크 캐시 |
| `agent` | 정규화 → 이해(`understand/`: 결정론 매처, 후속 지시, LLM) → 실행(`skills/`: 기기·알람·방송·날씨), 확인 질문 | 메모리 |
| `home` | HA WebSocket 클라이언트, 엔티티·area 캐시, 제어 정책(`policy.py`), 상태 변경 push | 메모리 |
| `alarm` | 허브 알람 사본, 앱 변경의 허브 전달과 확인 대기 | `alarm` |
| `hub` | 허브 등록, 이름·방(HA area) 지정 | `hub` |
| `broadcast` | 문장을 TTS로 만들어 대상 허브에 보냄 | 없음 |
| `weather` | Open-Meteo 예보 캐시와 허브 push | 메모리 |
| `telemetry` | 턴 기록(전사, 경로, 지연)과 요약 | `turn_log` |
| `system` | 관리자 전용 운영 API | 없음 |

공통 코드는 `core/`에 있다. 연결 관리(`connections.py`), 인증 의존성(`rest_auth.py`), WebSocket 접속 한도
(`ws_guard.py`), OpenAI 호출과 일일 한도(`openai_gateway.py`, `usage_guard.py`), SQLite 백업(`maintenance.py`)이다.

### 허브 (`hub/app/`)

| 패키지 | 책임 |
|---|---|
| `core/audio` | 단일 AudioRecord 캡처와 구독자 분배, openWakeWord ONNX 호출어, Silero VAD, 응답 재생 |
| `core/network` | 서버 WebSocket 연결, 재접속 대기, Ping/Pong, 서버 주소 검증 |
| `domain/voice` | 발화 상태 기계(`VoiceClient`) |
| `domain/usecase`, `data/scheduling` | 알람 예약(`AlarmCoordinator`, AlarmManager `setAlarmClock`), 스누즈 |
| `data/sync` | 서버와 알람 동기화 |
| `feature/*` | 앰비언트 시계, 대시보드, 알람 울림, 대화, 설정 화면(Compose) |
| `service` | 마이크·재생 포그라운드 서비스 |

토큰은 Android Keystore로 암호화해 DataStore에 둔다. 사용자 문구는 `res/values*/strings.xml`에 있다.

### 프로토콜 (`protocol/proto/cozy/v1/`)

모든 프레임은 `Envelope` 하나의 바이너리 직렬화다([ADR 0002](../adr/0002-protocol-buffers-over-websocket.md)).
`buf breaking`으로 main과 비교해 필드 번호 재사용과 삭제를 막는다. 서버는 `server/gen`에, 허브는 Gradle 빌드에서 같은
정의로 코드를 생성한다.

## 주요 흐름

### 음성 명령

1. 허브가 호출어를 감지하고 `UtteranceStart`와 `AudioChunk`를 보낸다. VAD가 끝을 판정하면 `UtteranceEnd`를 보낸다.
2. 여러 허브가 같은 말을 들으면 `arbiter`가 한 곳만 남기고 나머지에 `UtteranceDeclined`를 보낸다.
3. 서버가 STT로 전사하고 `Transcript`를 보낸다. `agent`가 결정론 매처를 먼저 시도하고, 실패하면 LLM 툴 루프로 넘긴다.
4. 기기 명령은 `home`이 정책을 확인한 뒤 HA `call_service`로 실행한다. 잠금·경보 패널은 어떤 경로로도 제어하지 않는다. 음성 경로에서는
   출입문 커버 열기, 허용 목록 밖 스크립트·장면, `COZY_VOICE_BLOCKED_ENTITIES`를 막는다(`home/policy.py`).
5. 응답 문장을 `ResponseText`로 보내고, TTS 오디오를 `ResponseAudioStart`·`AudioChunk`·`ResponseAudioEnd`로 스트리밍한다.

### 알람

허브가 알람을 소유하고 서버 없이도 울린다. 연결 직후 서버가 `AlarmSyncRequest`를 보내면 허브가 `AlarmSnapshot`으로
답한다. 음성이나 앱에서 온 변경은 서버가 `AlarmUpsert`·`AlarmDelete`로 전달하고, 허브의 `AlarmAck`로 확정한다. 음성 응답은
이 확인을 최대 3초 기다린 뒤 확정·거절·전달 중을 구분해 말한다. 허브가 거절한 변경은 `rejected`로 남기고 다시 보내지 않으며,
서버가 `AlarmSyncRequest`로 사본을 허브 값으로 되돌린다. 허브가 오프라인이면 사본을 대기 상태로 두었다가 재연결 때 보낸다.

### 허브 등록

관리자가 CLI나 `POST /api/auth/pairing-codes`로 6자리 1회용 코드를 발급한다. 코드에는 발급할 종류(hub·mobile)가 묶여 있다. 새 허브가 `POST /api/auth/pair`로 코드를
제출하면 `client_id`와 토큰을 받는다. 이후 `ClientHello`에 그 토큰을 담는다.

### HA 상태

서버는 HA WebSocket API로 상태 변경을 구독한다. 노출된 엔티티의 변경은 `EntityStateChanged`로 모든 클라이언트에
push한다. HA 수신 루프는 송신을 기다리지 않고 큐에 넣으며, 큐가 차면 쌓인 변경 대신 전체 `HomeSnapshot`을 보낸다. 클라이언트별 송신은
5초 제한이 있고, 넘긴 연결은 닫는다. 허브의 터치 명령은 `EntityCommand`로 들어와 같은 정책을 거쳐 실행된다.

## 제약과 근거

| 제약 | 근거 |
|---|---|
| AI 추론은 모두 클라우드에서 한다. 인터넷이 끊기면 음성 명령은 안 되고 알람과 터치 제어만 남는다 | [ADR 0001](../adr/0001-cloud-only-ai-inference.md) |
| 공급자는 OpenAI, 모델 이름은 설정으로만 지정한다(코드 기본값 없음) | [ADR 0003](../adr/0003-openai-as-llm-provider.md), [ADR 0004](../adr/0004-light-llm-tier-first.md) |
| 허브·앱 통신은 Protocol Buffers over WebSocket | [ADR 0002](../adr/0002-protocol-buffers-over-websocket.md) |
| HA 연동은 WebSocket API 한 곳이다. [서버 설계안](../design/0002-server-architecture.md)의 HA MCP 연동은 구현하지 않았다. 그 판단의 근거는 기록돼 있지 않다 | #88 |
| REST는 기본 거부(Bearer 토큰), `/api/system/*`은 관리자 전용 | [서버 보안 검토](../review/2026-11-19-server-security-review.md) |
| LAN 구간은 아직 평문이다 | [전체 적대적 검토](../review/2026-11-19-whole-project-adversarial-review.md), #89 |

이 문서는 현재 구조를 설명하고, 설계안(`docs/design/`)은 각 시점의 제안을 남긴다. 구조가 바뀌는 변경은 이 문서를
같은 PR에서 갱신한다. 코드 위치와 메시지 이름의 정본은 `server/app/`, `hub/app/`, `protocol/proto/`다.
