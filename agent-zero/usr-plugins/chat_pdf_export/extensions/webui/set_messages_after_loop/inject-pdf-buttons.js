// chat_pdf_export — inject a per-message "Export PDF" action button into
// every message's action bar (.step-action-buttons). Same hook + helper
// pattern used by _chat_branching/inject-branch-buttons.js.
//
// All export logic lives in the plugin's Alpine store
// (/plugins/chat_pdf_export/webui/chat-pdf-export-store.js). Importing
// it here registers the store if it hasn't been already (e.g. when the
// chat-input bottom button HTML hasn't loaded its <script> yet) and
// gives us a direct reference to call from the click handler.

import { createActionButton } from "/components/messages/action-buttons/simple-action-buttons.js";
import { store as pdfStore } from "/plugins/chat_pdf_export/webui/chat-pdf-export-store.js";

export default async function injectPdfButtons(context) {
  if (!context?.results?.length) return;

  for (const { args, result } of context.results) {
    if (!result?.element || args?.no == null) continue;

    const logNo = args.no;
    for (const bar of result.element.querySelectorAll(".step-action-buttons")) {
      // Idempotent: skip if we've already injected on this bar.
      if (bar.querySelector(".action-picture_as_pdf")) continue;

      bar.appendChild(
        createActionButton("picture_as_pdf", "이 메시지를 PDF 로 추출", function () {
          return pdfStore.export(logNo);
        }),
      );
    }
  }
}
