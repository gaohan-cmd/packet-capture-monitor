# Packet Capture Monitor 公网部署文档

本文档记录本项目在公网服务器上的完整部署流程。当前已验证的部署信息：

- 域名：`capture.example.com`
- 服务器公网 IP：`203.0.113.10`
- 服务器账号：`deploy`
- 项目目录：`/opt/packet-capture-monitor`
- 看板地址：`https://capture.example.com`
- 代理地址：`203.0.113.10:8081` 或 `capture.example.com:8081`

不要把服务器密码、看板密码、代理密码、`MONITOR_TOKEN`、`MONITOR_SESSION_SECRET` 提交到 Git。公网代理必须开启认证，只给明确授权的人使用。

## 架构

部署后会运行两个容器服务：

- `dashboard`：Web 看板和 API，只绑定服务器本机 `127.0.0.1:8765`，由 Nginx 对公网提供 HTTPS。
- `proxy`：mitmproxy 抓包代理，监听公网 `8081`，通过代理账号密码认证。

端口规划：

```text
80/tcp    Nginx HTTP，自动跳转 HTTPS，并用于 Let's Encrypt 验证
443/tcp   Nginx HTTPS，看板公网入口
8081/tcp  mitmproxy 抓包代理公网入口
8765/tcp  dashboard 容器端口，仅绑定 127.0.0.1
```

## 前置条件

1. 域名 `capture.example.com` 的 A 记录指向 `203.0.113.10`。
2. 云服务器安全组放行 `80`、`443`、`8081`。
3. 服务器已安装 Docker 和 Docker Compose。
4. 服务器账号 `deploy` 具备 `sudo` 权限。

检查域名解析：

```bash
dig +short capture.example.com A
```

期望输出：

```text
203.0.113.10
```

检查 Docker：

```bash
docker --version
docker compose version
```

如果当前用户不能直接访问 Docker，可在命令前加 `sudo`。下文默认使用 `sudo docker ...`，更贴近服务器上的实际部署方式。

## 上传代码

在本机项目根目录执行：

```bash
rsync -az --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude 'deploy/.env' \
  ./ deploy@capture.example.com:/tmp/packet-capture-monitor/
```

登录服务器后同步到正式目录：

```bash
ssh deploy@capture.example.com
sudo mkdir -p /opt/packet-capture-monitor
sudo rsync -a --delete --exclude 'deploy/.env' \
  /tmp/packet-capture-monitor/ /opt/packet-capture-monitor/
cd /opt/packet-capture-monitor
```

如果是首次部署，创建环境变量文件：

```bash
sudo cp deploy/.env.example deploy/.env
sudo chmod 600 deploy/.env
```

如果是升级部署，保留已有的 `deploy/.env`，不要覆盖它。

## 配置环境变量

编辑：

```bash
sudo nano /opt/packet-capture-monitor/deploy/.env
```

至少设置这些值：

```env
DASHBOARD_USERS=[{"username":"alice","password":"请改成强密码","proxy_username":"alice-proxy","proxy_password":"请改成强密码","target_url":"https://ikuuu.win","include_subdomains":true},{"username":"bob","password":"请改成强密码","proxy_username":"bob-proxy","proxy_password":"请改成强密码","target_url":"https://example.com","include_subdomains":true}]
MONITOR_SESSION_SECRET=至少32位随机字符串
MONITOR_TOKEN=至少32位随机字符串
MONITOR_TARGET_URL=https://ikuuu.win
MONITOR_INCLUDE_SUBDOMAINS=1
MONITOR_BODY_LIMIT=2097152
DASHBOARD_COOKIE_SECURE=1
```

生成随机字符串：

```bash
openssl rand -hex 32
```

字段说明：

- `DASHBOARD_USERS`：多用户配置，JSON 数组。每个用户都有独立看板账号、代理账号、默认抓包目标和数据空间。
- `username` / `password`：看板登录用户名和密码。
- `proxy_username` / `proxy_password`：浏览器代理认证用户名和密码。代理会用它识别数据归属。
- `target_url`：该用户默认采集目标。用户登录后还可以在看板顶部自行修改。
- `include_subdomains`：该用户是否默认采集子域名。
- `MONITOR_SESSION_SECRET`：看板登录 Session 加密密钥。
- `MONITOR_TOKEN`：`proxy` 向 `dashboard` 上报抓包事件时使用的内部令牌。
- `MONITOR_TARGET_URL`：全局默认采集目标。某个用户没有设置 `target_url` 时使用它。
- `MONITOR_INCLUDE_SUBDOMAINS`：全局默认子域名采集开关。
- `MONITOR_BODY_LIMIT`：请求体/响应体保存上限，默认 2MB。
- `DASHBOARD_COOKIE_SECURE`：公网 HTTPS 部署时保持 `1`。

兼容旧的单用户配置：

```env
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=请改成强密码
MITMPROXY_PROXY_AUTH=proxyuser:请改成强密码
```

如果设置了 `DASHBOARD_USERS`，系统优先使用多用户配置。

## 上传后自动部署

项目提供了服务器端部署脚本，适合你已经手动上传最新代码后快速重建服务。脚本不会上传代码。

首次部署或需要自动安装 Nginx/Certbot：

```bash
cd /opt/packet-capture-monitor
./deploy/server-deploy.sh \
  --domain capture.example.com \
  --email admin@example.com \
  --install-packages
```

日常更新代码或配置后重新部署：

```bash
cd /opt/packet-capture-monitor
./deploy/server-deploy.sh --domain capture.example.com
```

只重建 Docker 服务，不改 Nginx 和证书：

```bash
cd /opt/packet-capture-monitor
./deploy/server-deploy.sh --skip-nginx
```

常用选项：

- `--domain capture.example.com`：配置 Nginx 和 HTTPS 证书时使用的公网域名。
- `--email admin@example.com`：Let's Encrypt 注册邮箱；不填则使用无邮箱注册。
- `--skip-nginx`：跳过 Nginx 配置和证书处理。
- `--skip-certbot`：写入 Nginx 配置但不申请证书。
- `--no-build`：只执行 `docker compose up -d`，不重新构建镜像。
- `--install-packages`：通过 `apt-get` 安装 Nginx 和 Certbot。

脚本会自动检查 `deploy/.env`，拒绝使用占位密码或缺失的 `MONITOR_TOKEN` / `MONITOR_SESSION_SECRET`。

## 手动启动容器

```bash
cd /opt/packet-capture-monitor
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

正常状态应类似：

```text
NAME                 SERVICE     STATUS
deploy-dashboard-1   dashboard   Up
deploy-proxy-1       proxy       Up
```

确认端口监听：

```bash
sudo ss -tulpn | grep -E ':(8765|8081)'
```

期望看到：

```text
127.0.0.1:8765
0.0.0.0:8081
```

如果服务器位于中国大陆，构建镜像时可能出现 Debian 或 PyPI 下载很慢。可以临时在 `Dockerfile` 中改用可访问的 apt/pip 镜像源，构建完成后再按团队规范决定是否保留该改动。

## 安装 Nginx 和 Certbot

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx
```

先写入 HTTP 配置，用于访问看板和签发证书：

```bash
sudo mkdir -p /var/www/certbot
sudo nano /etc/nginx/conf.d/packet-capture-monitor.conf
```

内容如下：

```nginx
map $http_upgrade $connection_upgrade_packet_capture_monitor {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name capture.example.com;

    client_max_body_size 10m;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade_packet_capture_monitor;
        proxy_read_timeout 3600s;
    }
}
```

检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

从本机验证 HTTP 可达：

```bash
curl -I http://capture.example.com/
```

此时可能返回 `307` 到 `/login`，说明 Nginx 已经能代理到 dashboard。

## 签发 HTTPS 证书

使用 webroot 方式签发证书：

```bash
sudo certbot certonly \
  --webroot -w /var/www/certbot \
  -d capture.example.com \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email
```

证书路径：

```text
/etc/letsencrypt/live/capture.example.com/fullchain.pem
/etc/letsencrypt/live/capture.example.com/privkey.pem
```

签发成功后，把 Nginx 配置改为 HTTPS：

```bash
sudo nano /etc/nginx/conf.d/packet-capture-monitor.conf
```

内容如下：

```nginx
map $http_upgrade $connection_upgrade_packet_capture_monitor {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name capture.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name capture.example.com;

    ssl_certificate /etc/letsencrypt/live/capture.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/capture.example.com/privkey.pem;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade_packet_capture_monitor;
        proxy_read_timeout 3600s;
    }
}
```

检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

确认证书和自动续期：

```bash
sudo certbot certificates
systemctl list-timers certbot.timer --no-pager
```

## 部署验证

验证 HTTP 自动跳转 HTTPS：

```bash
curl -I http://capture.example.com/
```

期望包含：

```text
HTTP/1.1 301 Moved Permanently
Location: https://capture.example.com/
```

验证 HTTPS 看板：

```bash
curl -I https://capture.example.com/
curl https://capture.example.com/login | head
```

验证代理未认证会被拒绝：

```bash
curl --proxy http://capture.example.com:8081 http://mitm.it -I
```

期望返回：

```text
407 Proxy Authentication Required
```

验证代理认证可用：

```bash
PROXY_AUTH='alice-proxy:该用户的代理密码'
curl --proxy http://capture.example.com:8081 \
  --proxy-user "$PROXY_AUTH" \
  http://example.com/ -I
```

部分国内服务器或网络环境会把 `example.com`、`mitm.it` 等域名重定向到运营商或 DNSPod 的拦截页。只要未认证返回 `407`，认证后代理日志出现该请求，说明代理认证和公网转发已经正常。

查看日志：

```bash
cd /opt/packet-capture-monitor
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=80 proxy dashboard
```

## 常用运维命令

```bash
cd /opt/packet-capture-monitor

# 查看状态
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps

# 查看日志
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f

# 重启服务
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml restart

# 停止服务
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml down

# 升级代码后重新构建
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

备份数据：

```bash
sudo docker run --rm \
  -v deploy_capture-data:/data \
  -v "$PWD":/backup \
  busybox tar czf /backup/capture-data-backup.tgz -C /data .
```

备份 mitmproxy CA：

```bash
sudo docker run --rm \
  -v deploy_mitmproxy-ca:/ca \
  -v "$PWD":/backup \
  busybox tar czf /backup/mitmproxy-ca-backup.tgz -C /ca .
```

## 故障排查

### 代理日志出现 block_global

如果日志里出现：

```text
Client connection ... killed by block_global option.
```

说明 mitmproxy 拒绝公网客户端。当前代码在启用代理认证后会自动追加：

```text
--set block_global=false
```

请确认已经部署了当前版本，并且 `deploy/.env` 中配置了 `DASHBOARD_USERS` 或旧版 `MITMPROXY_PROXY_AUTH`。不要在未配置代理认证的情况下关闭 `block_global`。

### HTTPS 证书签发失败

检查：

```bash
dig +short capture.example.com A
curl -I http://capture.example.com/.well-known/acme-challenge/test
sudo nginx -t
sudo tail -n 80 /var/log/nginx/error.log
sudo tail -n 80 /var/log/letsencrypt/letsencrypt.log
```

常见原因：

- 域名没有解析到 `203.0.113.10`。
- 云服务器安全组没有开放 `80`。
- Nginx 配置没有保留 `/.well-known/acme-challenge/`。

### 看板能打开但没有抓包数据

检查：

- 浏览器是否配置了 `HTTP` 和 `HTTPS` 代理。
- 当前用户的 `proxy_username` / `proxy_password` 是否正确。
- 是否安装并信任了 mitmproxy CA 证书。
- 当前登录用户看板顶部的目标地址是否与实际访问网站一致。
- `MONITOR_TOKEN` 是否在 `dashboard` 和 `proxy` 中一致。

### 端口被占用

检查端口：

```bash
sudo ss -tulpn | grep -E ':(80|443|8081|8765)'
```

如果 `80` 或 `443` 被其他 Web 服务占用，需要合并 Nginx 配置或调整现有反向代理。

## 安全注意事项

- 代理会解密并展示 HTTPS 请求头、Cookie、Token、请求体和响应体，只能用于你拥有或明确授权调试的流量。
- 每个用户的 `proxy_password` 必须使用强密码。
- 不要把 `8081` 做成无认证开放代理。
- 部署完成后建议改掉曾经通过聊天、工单或临时渠道传递过的服务器密码。
- 建议使用 SSH key 登录，并关闭不必要的密码登录入口。
- 当前版本按用户隔离目标配置和抓包数据，但所有用户仍共用同一个 mitmproxy CA 和同一个代理端口。
