# 플러그인 작성 가이드

`agent-zero/usr-plugins/` 에 커스텀 플러그인을 추가할 때 참고하는 문서. 이 repo
의 두 레퍼런스 플러그인([chat_pdf_export](../agent-zero/usr-plugins/chat_pdf_export/),
[dashboard_link](../agent-zero/usr-plugins/dashboard_link/))을 만들면서 누적된
함정과 패턴을 정리한다.

> 새 플러그인을 만들 때 이 문서를 따라가면 슬롯 가시성·HTML 파싱·sys.path
> 충돌 같은 흔한 실수를 피할 수 있다.

---

## 1. 디렉토리 골격

```
agent-zero/usr-plugins/<name>/
├── plugin.yaml                           # 매니페스트 (아래 § 2 참조)
├── README.md                             # 사용자 시점 문서
├── api/                                  # ApiHandler — Flask blueprint 자동 등록
│   ├── __init__.py
│   └── <endpoint>.py                     # POST /api/plugins/<name>/<endpoint>
├── webui/                                # 글로벌 자산 (Alpine store, 폰트, 이미지)
│   └── <name>-store.js                   # /plugins/<name>/webui/<name>-store.js
└── extensions/
    └── webui/
        └── <slot-name>/                  # 어느 슬롯에 끼울지는 § 3 참조
            └── <name>-button.html        # 또는 .js (슬롯 종류에 따라)
```

**주의:**

- 패키지 이름은 [`agent-zero` 본체의 top-level 패키지](../agent-zero/) 와 충돌하지
  않게 짓는다. 특히 `helpers/` 는 `/a0/helpers/` 와 충돌해서 `import` 가 깨진다 —
  [chat_pdf_export](../agent-zero/usr-plugins/chat_pdf_export/README.md#why-render-not-helpers)
  는 그래서 `render/` 로 명명.
- `api/__init__.py` 가 있어야 Agent Zero 가 ApiHandler 를 발견한다 (빈 파일이라도 OK).

---

## 2. `plugin.yaml` 매니페스트

최소 4개 필드 (`name`, `title`, `description`, `version`) 만 있어도 동작하지만,
Plugin Hub 등재나 외부 발견성을 위해 메타데이터를 더 채우는 게 권장된다.

```yaml
name: <plugin_name>                        # 디렉토리명과 동일
title: <Plugin Title>                      # UI 표시명
description: <한 줄 요약>
version: 0.1.0                             # SemVer — fix=patch, feat=minor

# 메타데이터 (선택, Plugin Hub 권장)
author: <github-handle>
license: MIT
repo: https://github.com/<owner>/<repo>
homepage: https://github.com/<owner>/<repo>/tree/main/agent-zero/usr-plugins/<name>
tags:
  - <tag1>
  - <tag2>
```

**버전 규칙** — PR 머지 시점에 갱신. fix → patch (0.1.0 → 0.1.1), feat → minor
(0.1.1 → 0.2.0). [chat_pdf_export](../agent-zero/usr-plugins/chat_pdf_export/plugin.yaml)
가 0.1.0 → 0.2.1 까지 어떻게 올라갔는지 git log 참고.

---

## 3. WebUI 슬롯 가이드 — 어느 슬롯을 골라야?

Agent Zero 는 `extensions/webui/<slot-name>/` 의 파일을 슬롯에 자동 주입한다.
**슬롯 선택이 가시성을 결정**한다 — 잘못 고르면 사용자가 기능을 못 찾는다.

| 슬롯 | 가시성 | 적합한 용도 |
|---|---|---|
| `chat-input-bottom-actions-start` | **항상 보임** (Browser, Compact, Pause Agent 옆) | 전역 채팅 액션 — 가장 안전한 기본값 |
| `set_messages_after_loop` (JS extension) | 메시지별 `.step-action-buttons` 바 | 메시지 단위 액션 (copy/branch 옆) |
| `sidebar-quick-actions-dropdown-start` | collapsed 드롭다운 안 — 메뉴 펼쳐야 보임 | 자주 안 쓰는 액션, 또는 의도적으로 숨길 때 |

**다른 슬롯**은 Agent Zero 본체에서 `get_webui_extensions` 를 grep 해서 발견할 수
있다. 위 표는 이 repo 에서 검증된 것만 적었다.

**힌트**: 첫 플러그인이라면 `chat-input-bottom-actions-start` 부터 시작해라.
collapsed 드롭다운(`sidebar-quick-actions-dropdown-start`)에 두면 처음 본 사용자가
절대 못 찾는다.

---

## 4. Alpine store 패턴 — 인라인 `x-data` 안 쓰는 이유

### 왜

`extensions/webui/<slot>/<file>.html` 의 button 태그 attribute 안에서 멀티라인
`x-data` 나 화살표 함수 (`=>`) 를 쓰면 **HTML 파서가 `>` 를 만나서 button 태그를
조기 종료**한다. 예:

```html
<!-- 깨진 패턴 — 절대 쓰지 말 것 -->
<button x-data="{
  busy: false,
  click: () => { /* ... */ }   <!-- 여기서 button 이 닫힘 -->
}" @click="click()">
```

attribute 안에서 안전한 건 **단일 표현식**뿐이다. 로직이 한 줄을 넘으면 store 로
빼야 한다.

### 어떻게 — 표준 구조

**1) `webui/<plugin>-store.js`** — Alpine store 정의:

```js
import { fetchApi } from "/js/api.js";

export const store = {
  busy: false,
  async run() {
    if (this.busy) return;
    this.busy = true;
    try {
      const res = await fetchApi("/plugins/<name>/<endpoint>", { method: "POST" });
      // ...
    } finally {
      this.busy = false;
    }
  },
};

// alpine:init 등록 (정상 케이스)
document.addEventListener("alpine:init", function () {
  if (window.Alpine && !window.Alpine.store("<storeKey>")) {
    window.Alpine.store("<storeKey>", store);
  }
});

// 직접 등록 fallback — extension 자산이 alpine:init 이후에 로드됐을 때
if (window.Alpine && typeof window.Alpine.store === "function") {
  try {
    if (!window.Alpine.store("<storeKey>")) {
      window.Alpine.store("<storeKey>", store);
    }
  } catch (_) {}
}
```

**2) `extensions/webui/<slot>/<plugin>-button.html`** — store 임포트 + 호출:

```html
<html>
<head>
  <script type="module" src="/plugins/<name>/webui/<plugin>-store.js"></script>
</head>
<body>
  <template x-if="$store.<storeKey>">
    <button
      type="button"
      class="text-button <plugin>-action"
      :disabled="$store.<storeKey>.busy"
      @click="$store.<storeKey>.run()"
    >
      <p x-text="$store.<storeKey>.busy ? '...' : 'Run'"></p>
    </button>
  </template>
</body>
</html>
```

**핵심**:

- `<template x-if="$store.<storeKey>">` 가드 — store 가 등록되기 전 렌더되면
  `$store.<storeKey>` 가 `undefined` 라 에러
- `@click` 은 단일 표현식 (`$store.x.run()`) 만 — 화살표 함수 금지
- 파일이 `<html><head>...<body>` 로 감싸여 있어도 Agent Zero 가 슬롯에 끼울 때
  body 내용만 추출한다

레퍼런스 구현:
[chat-pdf-export-store.js](../agent-zero/usr-plugins/chat_pdf_export/webui/chat-pdf-export-store.js),
[dashboard-link-store.js](../agent-zero/usr-plugins/dashboard_link/webui/dashboard-link-store.js).

---

## 5. 메시지별 액션 버튼 (`set_messages_after_loop`)

채팅 안의 개별 메시지에 버튼을 붙이는 패턴. HTML 슬롯이 아니라 **JS extension**
이라 서명이 다르다.

```js
// extensions/webui/set_messages_after_loop/inject-<plugin>-buttons.js
import { createActionButton } from "/components/messages/action-buttons/simple-action-buttons.js";
import { store as myStore } from "/plugins/<name>/webui/<plugin>-store.js";

export default async function inject<Plugin>Buttons(context) {
  if (!context?.results?.length) return;

  for (const { args, result } of context.results) {
    if (!result?.element || args?.no == null) continue;
    const logNo = args.no;

    for (const bar of result.element.querySelectorAll(".step-action-buttons")) {
      // 멱등성: 이미 주입했으면 skip
      if (bar.querySelector(".action-<material-icon-name>")) continue;

      bar.appendChild(
        createActionButton(
          "<material-icon-name>",     // 예: "picture_as_pdf"
          "<툴팁 텍스트>",
          () => myStore.run(logNo),
        ),
      );
    }
  }
}
```

레퍼런스: [inject-pdf-buttons.js](../agent-zero/usr-plugins/chat_pdf_export/extensions/webui/set_messages_after_loop/inject-pdf-buttons.js).
같은 패턴을 `_chat_branching/inject-branch-buttons.js` (Agent Zero 본체) 에서도
볼 수 있다.

---

## 6. ApiHandler — 백엔드 엔드포인트

```python
# api/<endpoint>.py
from python.helpers.api import ApiHandler
from flask import Request, Response

class MyEndpoint(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool: return True
    @classmethod
    def requires_csrf(cls) -> bool: return True   # POST 면 True
    @classmethod
    def requires_api_key(cls) -> bool: return False

    async def process(self, input: dict, request: Request) -> dict | Response:
        # input 은 JSON body, request 는 Flask Request
        return {"ok": True}
```

자동 라우팅: 클래스가 `api/<endpoint>.py` 에 있으면 `POST /api/plugins/<name>/<endpoint>`.

레퍼런스:
[export_pdf.py](../agent-zero/usr-plugins/chat_pdf_export/api/export_pdf.py),
[get_token.py](../agent-zero/usr-plugins/dashboard_link/api/get_token.py).

---

## 7. 흔한 실수 + 디버그

| 증상 | 원인 | 해결 |
|---|---|---|
| 버튼이 깨져 보이거나 닫는 태그가 잘못 매칭 | `x-data` / `@click` attribute 안 화살표 함수 (`=>`) 가 `>` 를 닫는 태그로 해석 | Alpine store 로 분리 (§ 4) |
| 버튼 클릭 시 `$store.x` 가 `undefined` | store 등록 전 렌더 또는 storeKey 오타 | `<template x-if="$store.x">` 가드 + `alpine:init` + 직접 등록 fallback (§ 4) |
| 버튼이 안 보임 | 슬롯이 collapsed 드롭다운 안 | `chat-input-bottom-actions-start` 로 이동 (§ 3) |
| `import helpers.api` 가 빈 모듈 반환 | 플러그인의 `helpers/` 패키지가 `/a0/helpers/` 를 shadow | 패키지명을 `render/` 등 다른 이름으로 (§ 1) |
| 401/403 from API | CSRF 토큰 없이 호출 | `fetchApi` 사용 (자동 첨부, 직접 `fetch` 금지) |
| 새 파일이 컨테이너에 안 보임 | usr-plugins 가 마운트만 되고 reload 안 됨 | `docker compose up -d --force-recreate agent-zero` |
| WeasyPrint / weasyprint 의존 누락 | 베이스 이미지 변경 후 rebuild 안 함 | `docker compose build agent-zero` |

**경로 차이 주의**:

- ApiHandler 호출: `POST /api/plugins/<name>/<endpoint>` (`/api/` 접두사 있음)
- WebUI 자산: `/plugins/<name>/webui/<file>` (`/api/` 접두사 **없음**)

---

## 8. 레퍼런스 플러그인

| 플러그인 | 패턴 |
|---|---|
| [chat_pdf_export](../agent-zero/usr-plugins/chat_pdf_export/) | 전역 + 메시지별 두 진입점, ApiHandler 가 binary 응답 (PDF), Korean 폰트, render/templates/ 분리 |
| [dashboard_link](../agent-zero/usr-plugins/dashboard_link/) | 외부 서비스로 redirect, env 기반 token 노출, popup blocker fallback (clipboard) |
| `_chat_compaction` (Agent Zero 본체) | store + modal 패턴 — store 가 모달 상태도 관리하는 더 큰 사례 |
| `_chat_branching` (Agent Zero 본체) | `set_messages_after_loop` 의 `createActionButton` 사용 사례 |

---

## 9. 체크리스트

새 플러그인 PR 올리기 전에:

- [ ] `plugin.yaml` 에 `name/title/description/version/author/license/repo` 모두
- [ ] `README.md` — 무엇을 / 왜 / 어떻게 / API 표 / Configuration 표
- [ ] WebUI 자산은 Alpine store 패턴 (인라인 `x-data` 금지)
- [ ] 슬롯은 가시성 검증 (§ 3 표 참조)
- [ ] ApiHandler 는 `requires_auth`/`requires_csrf` 명시
- [ ] 새 의존성은 `agent-zero/Dockerfile` 에 `pip install` 라인 추가
- [ ] `docker compose build agent-zero && docker compose up -d --force-recreate agent-zero` 로 동작 검증
- [ ] 한국어 텍스트 / 파일명 사용 시 RFC 5987 (Content-Disposition) / `encodeURIComponent` 처리

작은 플러그인이면 위 항목 중 일부 (`Configuration`, `RFC 5987`) 는 생략 가능.
