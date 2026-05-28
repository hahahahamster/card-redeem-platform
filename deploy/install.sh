#!/usr/bin/env bash
set -euo pipefail

APP_NAME="card-redeem"
APP_DIR="${APP_DIR:-/opt/card-redeem}"
APP_USER="${APP_USER:-cardredeem}"
APP_PORT="${APP_PORT:-8787}"
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
ENABLE_SSL="${ENABLE_SSL:-auto}"
INSTALL_NGINX="${INSTALL_NGINX:-1}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请用 root 执行：sudo bash deploy/install.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> 安装系统依赖"
SHOULD_ENABLE_SSL="0"
if [[ "${INSTALL_NGINX}" == "1" && -n "${DOMAIN}" ]]; then
  if [[ "${ENABLE_SSL}" == "1" || ( "${ENABLE_SSL}" == "auto" && -n "${EMAIL}" ) ]]; then
    SHOULD_ENABLE_SSL="1"
  fi
fi

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3 nodejs npm rsync
  if [[ "${INSTALL_NGINX}" == "1" ]]; then
    apt-get install -y nginx
    if [[ "${SHOULD_ENABLE_SSL}" == "1" ]]; then
      apt-get install -y certbot python3-certbot-nginx
    fi
  fi
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 nodejs npm rsync
  if [[ "${INSTALL_NGINX}" == "1" ]]; then
    dnf install -y nginx
    if [[ "${SHOULD_ENABLE_SSL}" == "1" ]]; then
      dnf install -y certbot python3-certbot-nginx
    fi
  fi
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 nodejs npm rsync
  if [[ "${INSTALL_NGINX}" == "1" ]]; then
    yum install -y nginx
    if [[ "${SHOULD_ENABLE_SSL}" == "1" ]]; then
      yum install -y epel-release || true
      yum install -y certbot python3-certbot-nginx
    fi
  fi
else
  echo "不支持的 Linux 发行版，请手动安装 python3、nodejs、npm、rsync。"
  exit 1
fi

echo "==> 创建运行用户"
if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> 同步项目到 ${APP_DIR}"
mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude ".git" \
  --exclude "client/node_modules" \
  --exclude "client/dist" \
  --exclude "server/data/*.db" \
  --exclude "server/data/*.db-*" \
  --exclude "server/data/settings.json" \
  "${PROJECT_DIR}/" "${APP_DIR}/"

echo "==> 构建前端"
cd "${APP_DIR}/client"
npm install
npm run build

echo "==> 初始化数据目录权限"
mkdir -p "${APP_DIR}/server/data"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> 安装 systemd 服务"
sed "s|/opt/card-redeem|${APP_DIR}|g; s|CARD_PORT=8787|CARD_PORT=${APP_PORT}|g; s|User=cardredeem|User=${APP_USER}|g; s|Group=cardredeem|Group=${APP_USER}|g" \
  "${APP_DIR}/deploy/card-redeem.service" > /etc/systemd/system/${APP_NAME}.service
systemctl daemon-reload
systemctl enable ${APP_NAME}
systemctl restart ${APP_NAME}

if [[ "${INSTALL_NGINX}" == "1" ]]; then
  echo "==> 配置 Nginx 反向代理"
  SERVER_NAME="_"
  if [[ -n "${DOMAIN}" ]]; then
    SERVER_NAME="${DOMAIN//,/ }"
  fi

  sed "s|server_name _;|server_name ${SERVER_NAME};|g; s|127.0.0.1:8787|127.0.0.1:${APP_PORT}|g" \
    "${APP_DIR}/deploy/nginx-card-redeem.conf" > /etc/nginx/conf.d/card-redeem.conf
  nginx -t
  systemctl enable nginx
  systemctl reload nginx || systemctl restart nginx

  if [[ "${SHOULD_ENABLE_SSL}" == "1" ]]; then
    echo "==> 申请 HTTPS 证书"
    CERTBOT_DOMAIN_ARGS=()
    IFS=',' read -ra DOMAIN_LIST <<< "${DOMAIN}"
    for item in "${DOMAIN_LIST[@]}"; do
      clean_domain="$(echo "${item}" | xargs)"
      if [[ -n "${clean_domain}" ]]; then
        CERTBOT_DOMAIN_ARGS+=("-d" "${clean_domain}")
      fi
    done

    certbot --nginx \
      "${CERTBOT_DOMAIN_ARGS[@]}" \
      --non-interactive \
      --agree-tos \
      --email "${EMAIL}" \
      --redirect

    systemctl reload nginx || systemctl restart nginx
  elif [[ "${INSTALL_NGINX}" == "1" && -n "${DOMAIN}" ]]; then
    echo "==> 已配置域名 HTTP；如需自动 HTTPS，请用 EMAIL=你的邮箱 重新运行部署脚本。"
  fi
fi

echo "==> 部署完成"
echo "服务状态：systemctl status ${APP_NAME}"
echo "本机地址：http://127.0.0.1:${APP_PORT}"
if [[ "${INSTALL_NGINX}" == "1" ]]; then
  if [[ -n "${DOMAIN}" ]]; then
    FIRST_DOMAIN="${DOMAIN%%,*}"
    if [[ "${SHOULD_ENABLE_SSL}" == "1" ]]; then
      echo "访问地址：https://${FIRST_DOMAIN}"
    else
      echo "访问地址：http://${FIRST_DOMAIN}"
    fi
  else
    echo "访问地址：http://服务器IP"
  fi
fi
