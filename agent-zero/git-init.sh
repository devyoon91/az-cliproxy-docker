#!/bin/sh
# Agent Zero 컨테이너 내 Git 자동 설정

# Git user config
git config --global user.name "${GIT_USER_NAME}"
git config --global user.email "${GIT_USER_EMAIL}"

# GitHub PAT 기반 credential helper (토큰을 메모리에 저장)
git config --global credential.helper store
echo "https://${GIT_USER_NAME}:${GITHUB_TOKEN}@github.com" > /root/.git-credentials

echo "[git-init] Git configured for ${GIT_USER_NAME}"
