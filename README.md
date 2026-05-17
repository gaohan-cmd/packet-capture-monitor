# Packet Capture Monitor

Packet Capture Monitor 是一个用于显式代理调试的 HTTP(S) 请求可视化平台。它通过 mitmproxy 采集指定目标域名的请求，并在 Web 看板中展示请求头、请求体、响应头和响应体。

> 仅用于你自己拥有或明确授权调试的流量。HTTPS 内容展示依赖用户主动配置代理并安装 mitmproxy CA 证书，不能用于隐蔽拦截他人流量。

## 功能

- 仅采集指定目标地址，默认 `https://ikuuu.win`。
- 支持多用户，每个用户有独立看板账号、代理账号、目标配置和抓包数据。
- 支持子域名采集，可在配置中关闭。
- WebSocket 实时推送新增和更新的请求。
- 请求列表展示方法、状态码、路径、耗时、响应大小、内容类型。
- 详情视图展示请求头、响应头、请求体、响应体。
- 支持 JSON 格式化、文本/HTML 预览、图片预览、二进制摘要。
- SQLite 持久化保存采集记录。
- 默认隐藏 `Authorization`、`Cookie`、`Set-Cookie` 等敏感头。

## 文档

- 公网部署：见 [DEPLOYMENT.md](DEPLOYMENT.md)
- 使用说明：见 [USAGE.md](USAGE.md)

当前公网部署信息：

```text
看板地址: https://capture.example.com
服务器 IP: 203.0.113.10
代理端口: 8081
```

用户账号、代理账号、密码和内部令牌存放在服务器 `/opt/packet-capture-monitor/deploy/.env`，不要提交到 Git。

## 本地快速启动

建议使用 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

启动看板：

```bash
packet-monitor server --host 127.0.0.1 --port 8765
```

另开终端启动代理：

```bash
source .venv/bin/activate
packet-monitor proxy \
  --listen-host 127.0.0.1 \
  --listen-port 8081 \
  --target-url https://ikuuu.win
```

浏览器代理设置：

```text
HTTP Proxy:  127.0.0.1
HTTP Port:   8081
HTTPS Proxy: 127.0.0.1
HTTPS Port:  8081
```

首次抓 HTTPS 请求时，在已设置代理的浏览器中打开 `http://mitm.it`，下载并信任对应系统的 mitmproxy CA 证书。

## Docker 公网部署入口

```bash
cd /opt/packet-capture-monitor
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

完整流程见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 重要限制

当前版本按用户隔离看板配置和抓包数据，但所有用户仍共用同一个 mitmproxy CA 和同一个代理端口。只给明确授权的用户发放代理账号，调试结束后及时停用或更换对应密码。
