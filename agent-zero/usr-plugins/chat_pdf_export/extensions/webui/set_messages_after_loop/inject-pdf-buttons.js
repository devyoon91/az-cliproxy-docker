// chat_pdf_export — inject a per-message "Export PDF" action button into
// every message's action bar (.step-action-buttons). Same hook + helper
// pattern used by _chat_branching/inject-branch-buttons.js.
//
// Click → POST /plugins/chat_pdf_export/export_pdf with the active
// context id and the message's LogItem.no → blob download.

import { createActionButton } from "/components/messages/action-buttons/simple-action-buttons.js";

export default async function injectPdfButtons(context) {
  if (!context?.results?.length) return;

  for (const { args, result } of context.results) {
    if (!result?.element || args?.no == null) continue;

    const logNo = args.no;
    for (const bar of result.element.querySelectorAll(".step-action-buttons")) {
      // Idempotent: skip if we've already injected on this bar.
      if (bar.querySelector(".action-picture_as_pdf")) continue;

      bar.appendChild(
        createActionButton("picture_as_pdf", "이 메시지를 PDF 로 추출", async () => {
          const ctxid = globalThis.getContext?.();
          if (!ctxid) {
            alert("활성 채팅이 없습니다.");
            return;
          }

          try {
            const { fetchApi } = await import("/js/api.js");
            const res = await fetchApi("/plugins/chat_pdf_export/export_pdf", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              body: JSON.stringify({ context: ctxid, log_no: logNo }),
            });
            if (!res.ok) {
              const msg = await res.text();
              throw new Error(msg || "HTTP " + res.status);
            }
            const blob = await res.blob();

            // Filename: prefer RFC 5987 filename*=UTF-8'' over ASCII fallback.
            const disp = res.headers.get("Content-Disposition") || "";
            let filename = "chat-message.pdf";
            const star = disp.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
            if (star) {
              try { filename = decodeURIComponent(star[1]); } catch (_) {}
            } else {
              const plain = disp.match(/filename\s*=\s*"?([^";]+)"?/i);
              if (plain) filename = plain[1];
            }

            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 0);
          } catch (e) {
            alert("PDF 추출 실패: " + (e && e.message ? e.message : e));
          }
        }),
      );
    }
  }
}
