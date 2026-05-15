# 公网部署指南

这个项目可以部署为两个服务：

- `dashboard`：Web 看板和 API，只绑定服务器本机 `127.0.0.1:8765`，由 Nginx/Caddy 对公网提供 HTTPS。
- `proxy`：mitmproxy 抓包代理，对公网开放 `8081`，必须启用代理认证。

请只让获得授权的用户使用代理并安装 CA 证书。这个系统会展示请求头和响应体，里面可能包含 Cookie、Token、个人数据。

## 1. 准备服务器

服务器需要：

- 一个域名，例如 `capture.example.com`，A 记录指向服务器公网 IP。
- Docker 和 Docker Compose。
- Nginx 或其他反向代理。
- 防火墙开放 `80`、`443`、`8081`。

## 2. 上传项目

```bash
scp -r packet-capture-monitor root@YOUR_SERVER:/opt/packet-capture-monitor
ssh root@YOUR_SERVER
cd /opt/packet-capture-monitor
```

## 3. 配置环境变量

```bash
cp deploy/.env.example deploy/.env
nano deploy/.env
```

至少修改这些值：

```env
DASHBOARD_PASSWORD=一个强密码
MONITOR_SESSION_SECRET=至少32位随机字符串
MONITOR_TOKEN=至少32位随机字符串
MITMPROXY_PROXY_AUTH=proxyuser:一个强密码
MONITOR_TARGET_URL=https://ikuuu.win
```

可以生成随机字符串：

```bash
openssl rand -hex 32
```

## 4. 启动服务

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

看板容器只暴露到服务器本机：

```text
127.0.0.1:8765
```

代理容器对公网开放：

```text
YOUR_SERVER_IP:8081
```

## 5. 配置 Nginx

把 `deploy/nginx.conf` 里的 `capture.example.com` 替换成你的域名，然后复制到 Nginx 配置目录：

```bash
cp deploy/nginx.conf /etc/nginx/conf.d/packet-capture-monitor.conf
nano /etc/nginx/conf.d/packet-capture-monitor.conf
nginx -t
systemctl reload nginx
```

如果使用 Let's Encrypt，可以先签证书：

```bash
certbot --nginx -d capture.example.com
```

签完后确认 Nginx 配置里的证书路径和实际路径一致。

## 6. 用户如何使用

用户打开看板：

```text
https://capture.example.com
```

浏览器代理设置：

```text
HTTP Proxy:  YOUR_SERVER_IP
HTTP Port:   8081
HTTPS Proxy: YOUR_SERVER_IP
HTTPS Port:  8081
Username:    proxyuser
Password:    deploy/.env 中配置的代理密码
```

首次使用 HTTPS 抓包：

1. 用户配置代理后访问 `http://mitm.it`。
2. 下载对应系统证书。
3. 安装并信任该证书。
4. 再访问目标网站，看板会显示请求。

## 7. 常用运维命令

```bash
# 查看日志
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f

# 重启
docker compose --env-file deploy/.env -f deploy/docker-compose.yml restart

# 停止
docker compose --env-file deploy/.env -f deploy/docker-compose.yml down

# 升级代码后重新构建
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

## 重要限制

当前版本是共享看板：所有登录看板的人会看到同一个抓包数据池。如果你需要“每个用户独立账号、独立代理、独立数据隔离”，需要继续做多租户改造。
