#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="deploy/.env"
COMPOSE_FILE="deploy/docker-compose.yml"
DOMAIN="${DEPLOY_DOMAIN:-}"
EMAIL="${CERTBOT_EMAIL:-}"
SKIP_NGINX=0
SKIP_CERTBOT=0
SKIP_BUILD=0
INSTALL_PACKAGES=0
NGINX_CONF_PATH="/etc/nginx/conf.d/packet-capture-monitor.conf"

log() {
  printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

fail() {
  printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  deploy/server-deploy.sh [options]

Run this script on the server after you have uploaded the latest project files.
It does not upload code.

Options:
  --project-dir PATH      Project directory. Default: parent directory of this script.
  --env-file PATH         Env file path relative to project dir, or absolute. Default: deploy/.env
  --domain DOMAIN         Public dashboard domain, for example capture.example.com.
  --email EMAIL           Certbot email. If omitted, certbot uses --register-unsafely-without-email.
  --skip-nginx            Do not write/reload Nginx config.
  --skip-certbot          Do not request a Let's Encrypt certificate.
  --no-build              Run docker compose up -d without --build.
  --install-packages      Install nginx/certbot if missing via apt-get.
  -h, --help              Show this help.

Environment alternatives:
  DEPLOY_DOMAIN, CERTBOT_EMAIL
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --domain)
      DOMAIN="$2"
      shift 2
      ;;
    --email)
      EMAIL="$2"
      shift 2
      ;;
    --skip-nginx)
      SKIP_NGINX=1
      shift
      ;;
    --skip-certbot)
      SKIP_CERTBOT=1
      shift
      ;;
    --no-build)
      SKIP_BUILD=1
      shift
      ;;
    --install-packages)
      INSTALL_PACKAGES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
cd "$PROJECT_DIR"

if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="$PROJECT_DIR/$ENV_FILE"
fi
COMPOSE_PATH="$PROJECT_DIR/$COMPOSE_FILE"

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
fi

DOCKER=(docker)
if ! docker ps >/dev/null 2>&1; then
  DOCKER=($SUDO docker)
fi

COMPOSE=("${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_PATH")

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

load_env_value() {
  local key="$1"
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' "$ENV_FILE"
}

validate_env() {
  [[ -f "$ENV_FILE" ]] || fail "env file not found: $ENV_FILE. Copy deploy/.env.example to deploy/.env and edit it first."
  chmod 600 "$ENV_FILE" 2>/dev/null || true

  local monitor_token session_secret users_json legacy_password legacy_proxy
  monitor_token="$(load_env_value MONITOR_TOKEN)"
  session_secret="$(load_env_value MONITOR_SESSION_SECRET)"
  users_json="$(load_env_value DASHBOARD_USERS)"
  legacy_password="$(load_env_value DASHBOARD_PASSWORD)"
  legacy_proxy="$(load_env_value MITMPROXY_PROXY_AUTH)"

  [[ -n "$monitor_token" && "$monitor_token" != *change-this* ]] || fail "MONITOR_TOKEN is missing or still uses a placeholder."
  [[ -n "$session_secret" && "$session_secret" != *change-this* ]] || fail "MONITOR_SESSION_SECRET is missing or still uses a placeholder."

  if [[ -n "$users_json" ]]; then
    [[ "$users_json" != *change-this* ]] || fail "DASHBOARD_USERS still contains placeholder passwords."
  else
    [[ -n "$legacy_password" && "$legacy_password" != *change-this* ]] || fail "DASHBOARD_USERS is empty, so DASHBOARD_PASSWORD must be set for legacy single-user mode."
    [[ -n "$legacy_proxy" && "$legacy_proxy" != *change-this* && "$legacy_proxy" == *:* ]] || fail "DASHBOARD_USERS is empty, so MITMPROXY_PROXY_AUTH must be set as username:password."
  fi
}

install_packages_if_requested() {
  if [[ "$INSTALL_PACKAGES" -ne 1 ]]; then
    return
  fi
  require_command apt-get
  log "Installing nginx and certbot packages if needed"
  $SUDO apt-get update
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx
}

write_nginx_http_config() {
  local domain="$1"
  $SUDO mkdir -p /var/www/certbot "$(dirname "$NGINX_CONF_PATH")"
  $SUDO tee "$NGINX_CONF_PATH" >/dev/null <<NGINX
map \$http_upgrade \$connection_upgrade_packet_capture_monitor {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name ${domain};

    client_max_body_size 10m;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade_packet_capture_monitor;
        proxy_read_timeout 3600s;
    }
}
NGINX
}

write_nginx_https_config() {
  local domain="$1"
  $SUDO tee "$NGINX_CONF_PATH" >/dev/null <<NGINX
map \$http_upgrade \$connection_upgrade_packet_capture_monitor {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name ${domain};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${domain};

    ssl_certificate /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade_packet_capture_monitor;
        proxy_read_timeout 3600s;
    }
}
NGINX
}

reload_nginx() {
  require_command nginx
  $SUDO nginx -t
  $SUDO systemctl reload nginx
}

ensure_certificate() {
  local domain="$1"
  if [[ "$SKIP_CERTBOT" -eq 1 ]]; then
    log "Skipping certbot"
    return
  fi
  if [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" && -f "/etc/letsencrypt/live/${domain}/privkey.pem" ]]; then
    log "Certificate already exists for ${domain}"
    return
  fi

  require_command certbot
  log "Requesting Let's Encrypt certificate for ${domain}"
  if [[ -n "$EMAIL" ]]; then
    $SUDO certbot certonly --webroot -w /var/www/certbot -d "$domain" \
      --non-interactive --agree-tos --email "$EMAIL"
  else
    $SUDO certbot certonly --webroot -w /var/www/certbot -d "$domain" \
      --non-interactive --agree-tos --register-unsafely-without-email
  fi
}

configure_nginx_if_requested() {
  if [[ "$SKIP_NGINX" -eq 1 ]]; then
    log "Skipping Nginx configuration"
    return
  fi
  if [[ -z "$DOMAIN" ]]; then
    log "No --domain provided; skipping Nginx and certificate setup"
    return
  fi

  log "Writing temporary HTTP Nginx config for ${DOMAIN}"
  write_nginx_http_config "$DOMAIN"
  reload_nginx
  ensure_certificate "$DOMAIN"

  if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" && -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]]; then
    log "Writing HTTPS Nginx config for ${DOMAIN}"
    write_nginx_https_config "$DOMAIN"
    reload_nginx
  else
    log "Certificate is unavailable; leaving HTTP proxy config in place"
  fi
}

compose_up() {
  local up_args=(up -d)
  if [[ "$SKIP_BUILD" -ne 1 ]]; then
    up_args+=(--build)
  fi
  log "Starting Docker Compose services"
  "${COMPOSE[@]}" "${up_args[@]}"
}

health_check() {
  log "Docker Compose status"
  "${COMPOSE[@]}" ps

  log "Checking dashboard on 127.0.0.1:8765"
  curl -fsS --max-time 10 http://127.0.0.1:8765/login >/dev/null || fail "dashboard health check failed"

  if command -v ss >/dev/null 2>&1; then
    log "Listening ports"
    ss -tulpn | grep -E ':(80|443|8765|8081)[[:space:]]' || true
  fi

  if [[ -n "$DOMAIN" ]]; then
    log "Checking public HTTP endpoint"
    curl -I --max-time 10 "http://${DOMAIN}/" || true
    if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
      log "Checking public HTTPS endpoint"
      curl -I --max-time 10 "https://${DOMAIN}/" || true
    fi
  fi
}

main() {
  log "Project directory: $PROJECT_DIR"
  require_command docker
  require_command curl
  validate_env
  install_packages_if_requested
  compose_up
  configure_nginx_if_requested
  health_check
  log "Deployment finished"
}

main "$@"
