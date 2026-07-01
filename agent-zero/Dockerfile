# Extend the upstream agent-zero image with PDF rendering deps used by
# the local `chat_pdf_export` plugin. Cairo / Pango / fontconfig and the
# Noto Sans CJK font family are already present in the base image
# (LibreOffice depends on them), so weasyprint installs without any
# additional system packages.
#
# Bumping the base image: edit the FROM line and rebuild
# (`docker compose build agent-zero`).

FROM agent0ai/agent-zero:v2.1

RUN /opt/venv-a0/bin/pip install --no-cache-dir --disable-pip-version-check weasyprint
