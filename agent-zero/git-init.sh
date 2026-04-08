#!/bin/sh
# Agent Zero 컨테이너 내 Git + GitHub CLI 자동 설정

# Git user config
git config --global user.name "${GIT_USER_NAME}"
git config --global user.email "${GIT_USER_EMAIL}"

# GitHub PAT 기반 credential helper
git config --global credential.helper store
echo "https://${GIT_USER_NAME}:${GITHUB_TOKEN}@github.com" > /root/.git-credentials

# GitHub CLI (gh) 설치 (없으면)
if ! command -v gh > /dev/null 2>&1; then
    echo "[git-init] Installing GitHub CLI..."
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list
    apt-get update -qq > /dev/null 2>&1 && apt-get install -y -qq gh > /dev/null 2>&1
fi

# gh 인증 (PAT 토큰 사용)
if command -v gh > /dev/null 2>&1; then
    echo "${GITHUB_TOKEN}" | gh auth login --with-token 2>/dev/null
    echo "[git-init] GitHub CLI authenticated for ${GIT_USER_NAME}"
else
    echo "[git-init] WARNING: GitHub CLI installation failed, PR creation unavailable"
fi

echo "[git-init] Git configured for ${GIT_USER_NAME}"
