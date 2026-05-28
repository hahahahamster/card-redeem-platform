#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-card-redeem}"
APP_DIR="${APP_DIR:-/opt/card-redeem}"
APP_USER="${APP_USER:-cardredeem}"
REPO_OWNER="${CARD_UPDATE_REPO_OWNER:-hahahahamster}"
REPO_NAME="${CARD_UPDATE_REPO_NAME:-card-redeem-platform}"
BRANCH="${CARD_UPDATE_BRANCH:-main}"
ARCHIVE_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${BRANCH}.tar.gz"
LATEST_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/commits/${BRANCH}"
LOCK_FILE="${APP_DIR}/.update.lock"
LOG_FILE="${APP_DIR}/server/data/update.log"

if [[ -f "${LOCK_FILE}" ]]; then
  echo "已有更新任务正在执行：${LOCK_FILE}"
  exit 1
fi

cleanup() {
  rm -f "${LOCK_FILE}"
}
trap cleanup EXIT

touch "${LOCK_FILE}"
mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "==> $(date -Is) 开始更新 ${REPO_OWNER}/${REPO_NAME}@${BRANCH}"

TMP_DIR="$(mktemp -d)"
cleanup_tmp() {
  rm -rf "${TMP_DIR}"
}
trap 'cleanup; cleanup_tmp' EXIT

echo "==> 下载最新代码"
curl -fsSL "${ARCHIVE_URL}" -o "${TMP_DIR}/source.tar.gz"
tar -xzf "${TMP_DIR}/source.tar.gz" -C "${TMP_DIR}"
SRC_DIR="$(find "${TMP_DIR}" -maxdepth 1 -type d -name "${REPO_NAME}-*" | head -n 1)"
if [[ -z "${SRC_DIR}" ]]; then
  echo "未找到解压后的源码目录"
  exit 1
fi

echo "==> 同步代码，保留数据库和配置"
rsync -a --delete \
  --exclude ".git" \
  --exclude "client/node_modules" \
  --exclude "client/dist" \
  --exclude "server/data/*.db" \
  --exclude "server/data/*.db-*" \
  --exclude "server/data/settings.json" \
  "${SRC_DIR}/" "${APP_DIR}/"

echo "==> 构建前端"
cd "${APP_DIR}/client"
npm install
npm run build

echo "==> 写入版本号"
LATEST_SHA="$(python3 - <<PY
import json
import urllib.request

with urllib.request.urlopen("${LATEST_API_URL}", timeout=20) as response:
    data = json.load(response)
print(data.get("sha", ""))
PY
)"
if [[ -n "${LATEST_SHA}" ]]; then
  echo "${LATEST_SHA}" > "${APP_DIR}/VERSION"
fi

echo "==> 修正权限"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> 重启服务"
if [[ "$(id -u)" -eq 0 ]]; then
  systemctl restart "${APP_NAME}"
else
  sudo systemctl restart "${APP_NAME}"
fi

echo "==> $(date -Is) 更新完成"
