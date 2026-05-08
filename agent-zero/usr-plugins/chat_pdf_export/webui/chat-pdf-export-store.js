// Alpine store for the chat-pdf-export plugin.
// Holds `busy` + the `export(logNo?)` method so the inline button HTML
// stays trivial (no multi-line attribute, no arrow functions to clash
// with HTML parsing).
//
// Mirrors the pattern of _chat_compaction/webui/compact-store.js — the
// extension HTML imports this module via <script type="module" src=...>
// and dispatches with $store.chatPdfExport.export().

import { fetchApi } from "/js/api.js";

function _filenameFromContentDisposition(disp) {
  if (!disp) return "chat-export.pdf";
  const star = disp.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  if (star) {
    try { return decodeURIComponent(star[1]); } catch (_) {}
  }
  const plain = disp.match(/filename\s*=\s*"?([^";]+)"?/i);
  if (plain) return plain[1];
  return "chat-export.pdf";
}

export const store = {
  busy: false,

  /**
   * POST to /plugins/chat_pdf_export/export_pdf and trigger a browser
   * download of the returned PDF blob.
   *
   * @param {number|null} logNo  Optional LogItem.no for per-message export.
   *                             Omit / null for full-chat export.
   */
  async export(logNo) {
    if (this.busy) return;
    const ctxid = globalThis.getContext && globalThis.getContext();
    if (!ctxid) {
      alert("활성 채팅이 없습니다.");
      return;
    }
    this.busy = true;
    try {
      const body = { context: ctxid };
      if (logNo !== undefined && logNo !== null) body.log_no = logNo;

      const res = await fetchApi("/plugins/chat_pdf_export/export_pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const msg = await res.text();
        throw new Error(msg || ("HTTP " + res.status));
      }
      const blob = await res.blob();
      const filename = _filenameFromContentDisposition(
        res.headers.get("Content-Disposition")
      );

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    } catch (e) {
      alert("PDF 추출 실패: " + (e && e.message ? e.message : e));
    } finally {
      this.busy = false;
    }
  },
};

// Register the Alpine store. Same idempotency guard pattern as other plugins.
document.addEventListener("alpine:init", function () {
  if (window.Alpine && !window.Alpine.store("chatPdfExport")) {
    window.Alpine.store("chatPdfExport", store);
  }
});

// If Alpine has already initialized by the time this module loads
// (extension assets can land late), register directly.
if (window.Alpine && typeof window.Alpine.store === "function") {
  try {
    if (!window.Alpine.store("chatPdfExport")) {
      window.Alpine.store("chatPdfExport", store);
    }
  } catch (_) {}
}
