---
type: guide
status: active
---

# Cozy Buddy

집 안에서 직접 운영하는 스마트홈 음성 비서 프로젝트의 모노레포다. 전체 설계 문서와 모든 컴포넌트
코드를 한 Git 히스토리에 둔다. 서버·허브·휴대폰 앱이 같은 통신 프로토콜을 공유하므로, 프로토콜
변경과 그에 따른 각 컴포넌트 수정을 한 커밋으로 묶기 위해서다.

## 레포 구성

| 경로 | 역할 | 기술 스택 |
|---|---|---|
| `docs/` | 설계 문서. 현재 구조는 [`docs/architecture/README.md`](docs/architecture/README.md) | Markdown |
| `protocol/` | 허브·앱·서버 통신 스키마 | Protocol Buffers, buf |
| `server/` | 서버 | Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite, Docker |
| `hub/` | 방마다 두는 태블릿 허브 앱 | Kotlin, Jetpack Compose, Hilt |
| `mobile/` | 휴대폰 앱 | 미생성. 모든 단계의 마지막에 구현 |
| `spikes/` | 스파이크 실험 코드와 결과 | Python |

## 문서 위치

문서 유형과 위치는 `.engsys/project.yaml`의 `documentation.authoring`이 정한다.

| 경로 | 유형 | 답하는 질문 |
|---|---|---|
| `docs/review/` | review-report | 어떤 결함이 어떤 영향을 주며 무엇부터 고쳐야 하는가 |
| `docs/scope/` | project-scope | 무엇을 제공하며 어디까지 하면 완료인가 |
| `docs/spike/` | experiment-report | 무엇을 실측했고 어디까지 적용할 수 있는가 |
| `docs/adr/` | decision-record | 무엇을 왜 선택했는가 |
| `docs/design/` | design-proposal | 이 범위와 방법으로 구현해도 되는가 |
| `docs/architecture/` | architecture | 지금 구성요소가 어떤 책임과 경계로 동작하는가 |
| `docs/plan/` | work-plan | 무엇을 언제까지 하며 무엇이 막고 있는가 |
| `docs/ui/` | ui-guideline | 허브·앱 UI를 어떤 규칙으로 구현하는가 |
| `docs/diagrams/` | 검사 제외 | 설계안의 구조를 그린 그림 (`architecture.html`을 브라우저로 연다) |

## 이전 시도

2025-10부터 2026-07까지 진행한 이전 구현의 원문은 옛 레포의 고정 revision에서 본다.

- 설계서·기능 명세·리서치: [cozy-buddy-server `docs/`](https://github.com/pis-o-cake/cozy-buddy-server/blob/6f8fbe6fbdc7d00c33e03d11dc9cec4eafe6d6ac/docs) (revision `6f8fbe6`). v2 설계서는 `docs/archive/`에 있다.
- 허브 Compose UI: [cozy-buddy-hub `app/`](https://github.com/pis-o-cake/cozy-buddy-hub/tree/e8505c84115a1b8ebc2d41317b7655800fcb21d7/app) (revision `e8505c8`)
- 1차 서버 구현: [cozy-buddy-server](https://github.com/pis-o-cake/cozy-buddy-server/tree/5f2aabcb0cd800cf12f52ca815b5648315e1d2ed) (revision `5f2aabc`)

재설계는 이 원문을 그대로 계승하지 않고, `docs/review/`의 검토 결과를 거쳐 다시 결정한다.
허브 UI 방향(앰비언트 시계 화면)은 유지 대상이다.

## 시작하기

이 레포는 [Engineering System](https://github.com/pis-o-cake/engineering-system) 계약을 따른다.

```sh
git config core.hooksPath .githooks   # clone 직후 한 번. 커밋 전 비밀정보 검사
engsys doctor          # 계약·hook 상태 진단
sh scripts/verify.sh   # 문서 계약, 비밀정보 검사, 변경된 컴포넌트 검사
```
