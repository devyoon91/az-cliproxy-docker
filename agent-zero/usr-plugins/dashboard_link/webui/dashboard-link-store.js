// Alpine store for the dashboard_link plugin.
// Fetches DASHBOARD_TOKEN from the backend (which reads it from
// agent-zero's env, where it lives because of `env_file: .env`),
// builds the URL using the user's current browser hostname so
// SSH-tunneled and direct setups both work, and opens the dashboard
// in a new tab.
//
// Mirrors chat_pdf_export's store pattern — keeps the inline button
// HTML attribute-safe (no multi-line x-data, no arrow functions).

import { fetchApi } from "/js/api.js";

export const store = {
  busy: false,

  async open() {
    if (this.busy) return;
    this.busy = true;
    try {
      const res = await fetchApi("/plugins/dashboard_link/get_token", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!res.ok) {
        const msg = await res.text();
        throw new Error(msg || ("HTTP " + res.status));
      }
      const data = await res.json();
      const token = data && data.token;
      const port = (data && data.port) || 8443;
      if (!token) {
        throw new Error("Empty DASHBOARD_TOKEN — set it in .env and recreate agent-zero");
      }

      const proto = window.location.protocol;
      const host = window.location.hostname;
      const url = proto + "//" + host + ":" + port +
                  "/dashboard?token=" + encodeURIComponent(token);

      // Open in a new tab. `noopener,noreferrer` so the new tab can't
      // navigate the agent-zero tab back via window.opener.
      const win = window.open(url, "_blank", "noopener,noreferrer");
      if (!win) {
        // Popup blocker fallback — copy URL to clipboard so the user
        // can paste it manually.
        try {
          await navigator.clipboard.writeText(url);
          alert("팝업이 차단되었습니다. URL 을 클립보드에 복사했습니다 — 새 탭에 붙여넣기.");
        } catch (_) {
          alert("팝업이 차단되었습니다. 이 URL 을 직접 열어주세요:\n" + url);
        }
      }
    } catch (e) {
      alert("대시보드 열기 실패: " + (e && e.message ? e.message : e));
    } finally {
      this.busy = false;
    }
  },
};

// Register store on Alpine init. Mirror the chat_pdf_export idempotency.
document.addEventListener("alpine:init", function () {
  if (window.Alpine && !window.Alpine.store("dashboardLink")) {
    window.Alpine.store("dashboardLink", store);
  }
});

if (window.Alpine && typeof window.Alpine.store === "function") {
  try {
    if (!window.Alpine.store("dashboardLink")) {
      window.Alpine.store("dashboardLink", store);
    }
  } catch (_) {}
}
