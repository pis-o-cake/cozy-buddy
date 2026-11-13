---
type: guide
status: active
---

# Cozy Buddy

집 안에서 직접 운영하는 스마트홈 음성 비서 프로젝트의 모노레포다. 전체 설계 문서와 모든 컴포넌트
코드를 한 Git 히스토리에 둔다. 서버·허브·휴대폰 앱이 같은 통신 프로토콜을 공유하므로, 프로토콜
변경과 그에 따른 각 컴포넌트 수정을 한 커밋으로 묶기 위해서다.

## 레포 구성

| 경로 | 역할 | 상태 |
|---|---|---|
| `docs/` | 전체 설계 문서 | 재설계 진행 중 |
| `protocol/` | 컴포넌트 간 통신 스키마 | 미생성 |
| `server/` | 서버 | 미생성 |
| `hub/` | 방마다 두는 태블릿 허브 앱 | 미생성 |
| `mobile/` | 휴대폰 앱 | 미생성 |

컴포넌트의 기술 스택은 아직 정하지 않았다. 스파이크와 ADR로 결정한 뒤 디렉토리를 만든다.

## 문서 위치

문서 유형과 위치는 `.engsys/project.yaml`의 `documentation.authoring`이 정한다.

| 경로 | 유형 | 답하는 질문 |
|---|---|---|
| `docs/review/` | review-report | 이전 설계의 어떤 판단이 틀렸거나 낡았는가 |
| `docs/scope/` | project-scope | 무엇을 제공하며 어디까지 하면 완료인가 |
| `docs/spike/` | experiment-report | 무엇을 실측했고 어디까지 적용할 수 있는가 |
| `docs/adr/` | decision-record | 무엇을 왜 선택했는가 |
| `docs/design/` | design-proposal | 이 범위와 방법으로 구현해도 되는가 |
| `docs/architecture/` | architecture | 지금 구성요소가 어떤 책임과 경계로 동작하는가 |
| `docs/plan/` | work-plan | 무엇을 언제까지 하며 무엇이 막고 있는가 |
| `docs/ui/` | ui-guideline | 허브·앱 UI를 어떤 규칙으로 구현하는가 |
| `docs/diagrams/` | 검사 제외 | 설계안의 구조를 그린 그림 (`architecture.html`을 브라우저로 연다) |
| `docs/legacy/` | 검사 제외 | 이전 시도(2025~2026-07)의 원문 사본 |

## 이전 시도

2025-10부터 2026-07까지 진행한 이전 구현의 설계서와 리서치 원문은 `docs/legacy/`에 그대로 보존한다.
재설계는 이 원문을 그대로 계승하지 않고, `docs/review/`의 검토 결과를 거쳐 다시 결정한다.
허브 UI 방향(앰비언트 시계 화면)은 유지 대상이다.

## 시작하기

이 레포는 [Engineering System](https://github.com/pis-o-cake/engineering-system) 계약을 따른다.

```sh
engsys doctor     # 계약·hook 상태 진단
engsys verify     # 문서 구조 검사 + 편집 검토 기록 검사
```
