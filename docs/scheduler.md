# Agent Zero 스케줄러 가이드

> 원본: [agent0ai/agent-zero 스케줄러 프롬프트](https://github.com/agent0ai/agent-zero) (MIT License)

---

## 개요

Agent Zero 내장 스케줄러로 작업을 자동 실행할 수 있습니다. cron 문법 기반 반복 실행, 특정 시간 실행, 수동 실행을 지원합니다.

태스크 실행 시 별도 컨텍스트(채팅)에서 백그라운드로 수행되며, 기존 대화에 영향을 주지 않습니다.

---

## 태스크 타입

| 타입 | 설명 | 실행 방식 |
|------|------|-----------|
| **Scheduled** | cron 문법으로 반복 실행 | 자동 (crontab) |
| **Planned** | 특정 날짜/시간 목록에 실행 | 자동 (시간 도달 시) |
| **AdHoc** | 수동 실행 전용 | UI 또는 `scheduler:run_task`로 직접 실행 |

---

## 사용 방법 (Agent Zero에게 지시)

### 반복 작업 등록 (Scheduled)

> "매일 오전 9시에 서버 상태 체크하고 결과를 Telegram으로 알려줘"

> "매 30분마다 GitHub에서 새 이슈 확인해줘"

> "매주 월요일 오전 10시에 주간 보고서 만들어줘"

### 예약 실행 (Planned)

> "내일 오후 3시에 데이터베이스 백업해줘"

> "4월 15일 오전 9시, 4월 20일 오전 9시에 리포트 생성해줘"

### 수동 실행 (AdHoc)

> "배포 태스크 만들어줘. 내가 직접 실행할게"

---

## 스케줄러 도구 목록

### scheduler:list_tasks — 태스크 목록 조회

필터 옵션:
- `state`: `idle`, `running`, `disabled`, `error`
- `type`: `adhoc`, `planned`, `scheduled`
- `next_run_within`: N분 이내 실행 예정
- `next_run_after`: N분 이후 실행 예정

### scheduler:find_task_by_name — 이름으로 검색

태스크 이름(부분 일치)으로 검색합니다.

### scheduler:show_task — 태스크 상세 조회

UUID로 특정 태스크의 전체 설정을 확인합니다.

### scheduler:create_scheduled_task — 반복 태스크 생성

cron 문법으로 반복 실행 태스크를 생성합니다.

**cron 문법 (5필드):**
```
분  시  일  월  요일
*   *   *   *   *
```

| 예시 | 설명 |
|------|------|
| `*/5 * * * *` | 5분마다 |
| `0 9 * * *` | 매일 오전 9시 |
| `0 9 * * 1` | 매주 월요일 오전 9시 |
| `0 */2 * * *` | 2시간마다 |
| `30 8 1 * *` | 매월 1일 오전 8시 30분 |

**파라미터:**
- `name`: 태스크 이름
- `system_prompt`: 태스크 실행 시 시스템 프롬프트
- `prompt`: 실제 작업 지시
- `schedule`: `{minute, hour, day, month, weekday}` cron 필드
- `attachments`: 첨부 파일 경로/URL 목록
- `dedicated_context`: `true`면 별도 채팅에서 실행 (권장)

### scheduler:create_planned_task — 예약 태스크 생성

특정 날짜/시간 목록에 실행되는 태스크를 생성합니다.

**파라미터:**
- `plan`: ISO 8601 형식 날짜 목록 (예: `["2025-04-29T18:25:00"]`)
- 나머지는 scheduled_task와 동일

### scheduler:create_adhoc_task — 수동 태스크 생성

스케줄 없이 수동으로만 실행하는 태스크를 생성합니다.

### scheduler:run_task — 수동 실행

UUID로 태스크를 즉시 실행합니다. `context` 파라미터로 추가 정보를 전달할 수 있습니다.

### scheduler:delete_task — 태스크 삭제

UUID로 태스크를 삭제합니다.

### scheduler:wait_for_task — 완료 대기

태스크가 실행 중이면 완료를 기다리고 결과를 반환합니다.
`dedicated_context=true`인 태스크만 대기 가능합니다.

---

## 실전 예시

### 매일 서버 상태 체크 + Telegram 알림

Agent Zero에게:
> "매일 오전 9시에 서버 상태를 체크하는 스케줄 태스크를 만들어줘. 
> 결과는 curl로 http://telegram-bridge:8443/notify 에 POST로 보내줘.
> 별도 컨텍스트에서 실행해줘."

### 매 시간 GitHub 이슈 체크

> "1시간마다 https://github.com/username/project 의 새 이슈를 확인하고, 
> 새 이슈가 있으면 Telegram으로 알려줘."

### 배포 전 테스트 자동화

> "adhoc 태스크로 '배포 전 테스트'를 만들어줘. 
> /a0/work_dir/my-project에서 테스트 실행하고 결과를 알려줘."

실행할 때:
> "배포 전 테스트 태스크 실행해줘"

---

## 주의사항

- 스케줄 태스크는 **컨테이너가 실행 중일 때만** 동작합니다 (컨테이너 재시작 시 태스크 재로드)
- `dedicated_context: true` 권장 — 별도 채팅에서 실행되어 기존 대화에 영향 없음
- 재귀적 태스크 생성 주의 — 태스크가 또 다른 태스크를 만드는 프롬프트는 피하세요
- 태스크 결과를 Telegram으로 받으려면 curl webhook 활용
