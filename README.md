# Packet Capture Monitor

本项目是一个本机自用的 HTTPS 请求可视化平台，默认监听 `https://ikuuu.win/` 域名及其子域名下的请求，并展示每条请求的请求头、请求体、响应头和响应数据。

> 请只用于你自己拥有或有授权调试的浏览器流量。HTTPS 内容需要显式配置本机代理并安装 mitmproxy 的本地 CA 证书，不能隐蔽拦截他人流量。

## 功能

- 仅采集目标地址：默认 `https://ikuuu.win/`，可在看板顶部自定义。
- 实时看板：WebSocket 推送新增和更新的请求。
- 请求列表：方法、状态码、路径、耗时、响应大小、内容类型。
- 详情视图：概览、请求头、响应头、请求体、响应体。
- 响应可视化：JSON 格式化、文本/HTML 预览、图片预览、二进制十六进制摘要。
- 本地持久化：SQLite 保存采集记录。
- 敏感头保护：界面默认隐藏 `Authorization`、`Cookie`、`Set-Cookie` 等头，可手动显示。

## 安装

建议使用 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 启动看板

```bash
source .venv/bin/activate
packet-monitor server --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

## 启动 HTTPS 采集代理

另开一个终端：

```bash
source .venv/bin/activate
packet-monitor proxy --listen-host 127.0.0.1 --listen-port 8081 --target-url https://ikuuu.win
```

然后把浏览器或系统代理设置为：

```text
HTTP  Proxy: 127.0.0.1:8081
HTTPS Proxy: 127.0.0.1:8081
```

首次使用 HTTPS 解密时，需要安装并信任 mitmproxy CA 证书：

1. 保持代理运行。
2. 在已设置代理的浏览器中打开 `http://mitm.it`。
3. 下载并安装对应系统的证书。
4. macOS 需要在“钥匙串访问”中把该证书设置为始终信任。

完成后访问 `https://ikuuu.win/`，看板会实时显示该域名相关请求。

## 常用配置

```bash
# 修改默认目标地址，也可以直接在看板顶部输入并应用
packet-monitor server --target-url https://example.com
packet-monitor proxy --target-url https://example.com

# 不采集子域名
packet-monitor proxy --target-url https://ikuuu.win --no-include-subdomains

# 调整响应体预览上限，默认 2MB
packet-monitor proxy --body-limit 1048576

# 修改 SQLite 路径
CAPTURE_DB_PATH=/tmp/captures.sqlite3 packet-monitor server
```

可选本地 token：

```bash
export MONITOR_TOKEN="change-me"
packet-monitor server
packet-monitor proxy
```

## 说明

这个工具采集的是通过本机代理显式经过 mitmproxy 的 HTTP(S) 流量。它不会读取系统里未代理的网络连接，也不会绕过 HTTPS；HTTPS 内容展示依赖用户主动安装本地 CA 证书。

## 公网部署

已提供 Docker Compose 和 Nginx 示例，见 [DEPLOYMENT.md](DEPLOYMENT.md)。
