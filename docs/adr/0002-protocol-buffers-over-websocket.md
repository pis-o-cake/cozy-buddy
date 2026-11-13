---
type: decision-record
status: accepted
date: 2026-11-13
---

# 클라이언트-서버 실시간 계약은 WebSocket 위의 Protocol Buffers로 정의한다

허브·휴대폰 앱과 Cozy Server 사이의 실시간 통신은 WebSocket 연결 하나에 Protocol Buffers(protobuf)로 인코딩한
`Envelope` 메시지를 바이너리 프레임으로 주고받는다. 제어 메시지와 오디오 조각이 같은 정의를 쓴다. 정의 파일은
`protocol/proto/`가 유일한 정본이며, Python과 Kotlin 코드는 여기서 생성한다.

## 결정

- 연결: 클라이언트마다 WebSocket 하나(`/ws`). 허브와 휴대폰 앱이 같은 엔드포인트를 쓰고, 첫 메시지
  `ClientHello`로 클라이언트 종류와 토큰을 밝힌다.
- 인코딩: 모든 프레임은 바이너리이고 내용은 `cozy.v1.Envelope` 하나다. 텍스트 프레임은 쓰지 않는다.
- 메시지 구조: `Envelope`는 송신 순번과 `oneof body`를 가진다. 오디오는 `AudioChunk` 메시지로 보내며 스트림
  종류(발화 올림, 응답 내림, 방송 내림), 스트림 ID, 스트림 내 순번, PCM 바이트를 담는다.
  [이전 설계 검토 M3](../review/2026-11-13-legacy-design-adversarial-review.md)의 결함(오디오 프레임에
  세션·순번이 없고 방송과 응답을 구분할 수 없음)을 구조로 막는다.
- 정본과 생성:
  - `protocol/proto/cozy/v1/*.proto`, 패키지 `cozy.v1`.
  - Kotlin(허브·앱)은 Gradle protobuf 플러그인의 lite 런타임으로 이 디렉토리를 직접 컴파일한다.
  - Python(서버)은 고정 버전의 `grpcio-tools`로 생성한다. 생성 코드는 커밋하지 않고, 테스트와 Docker 빌드 전에 만든다.
- 호환성: `buf lint`와 `buf breaking`(main 기준)을 검사에 넣는다. 깨지는 변경이 필요하면 `cozy.v2`를 만든다.
- REST: 페어링, 헬스체크, 관리용 조회처럼 실시간이 아닌 것만 REST로 두고 FastAPI가 생성하는 OpenAPI(Swagger UI)를 정본으로 한다.

## 배경과 제약

서버는 Python, 허브와 휴대폰 앱은 Kotlin이다. 세 컴포넌트가 같은 메시지를 다루므로 정의가 하나여야 하고,
모노레포를 택한 이유도 프로토콜 변경과 그에 따른 수정을 한 커밋으로 묶기 위해서였다. 연결 하나로 제어와
오디오를 함께 보내야 하고, 오디오는 20ms 단위 PCM이라 프레임 수가 많다. 메시지 종류는 음성, 알람 동기화,
기기 상태로 계속 늘어나므로, 여러 종류 중 하나를 담는 메시지(태그된 합 타입)를 양쪽 언어에서 손으로 맞추지
않고 타입 안전하게 다룰 수 있어야 한다.

## 근거와 대안

| 대안 | 내용 | 판단 |
|---|---|---|
| Protocol Buffers over WebSocket (채택) | 제어·오디오 모두 protobuf 바이너리 | `oneof`가 Python·Kotlin 양쪽에서 타입 안전한 합 타입으로 생성된다. 오디오를 별도 헤더 규칙 없이 메시지 필드로 담는다. 필드 추가가 하위 호환이고 `buf breaking`으로 기계 검사된다 |
| JSON 텍스트 + JSON Schema 코드 생성 | 제어는 JSON, 오디오는 바이너리 프레임에 자체 헤더 | 사람이 읽기 쉽다. 그러나 판별 필드 기반 합 타입의 Kotlin 생성 품질이 낮고, 오디오 헤더는 스키마 밖의 별도 규칙이 된다. 정의 방식이 두 개로 갈린다 |
| JSON + 양쪽 손작성 모델 | 이전 설계 방식 | 언어 사이에서 정의가 어긋나도 검사할 수 없다 |
| gRPC 양방향 스트리밍 | protobuf + HTTP/2 | 계약 방식은 같지만 Nginx·Android 쪽 HTTP/2 구성과 런타임 크기가 늘고, WebSocket 하나로 충분한 요구에 비해 이득이 없다 |

## 영향

- 디버깅에 디코더가 필요하다. `protocol/`에 프레임을 사람이 읽는 형태로 바꾸는 개발용 도구를 둔다.
- 개발 환경에 `buf` CLI가 필요하다. 서버 검사에는 `grpcio-tools`가 들어간다.
- 클라우드 실시간 음성 API를 채택하더라도(S2) 이 계약은 허브와 서버 사이에만 적용된다. 서버와 공급자 사이는
  공급자 SDK를 따른다.
- 휴대폰 앱도 실시간 상태 수신과 명령을 이 계약으로 하므로, REST 클라이언트는 페어링과 헬스체크만 구현한다.
  [휴대폰 앱 설계안](../design/0004-mobile-architecture.md)의 "REST + 상태 WebSocket"은 이 결정에 맞춰 읽는다.
