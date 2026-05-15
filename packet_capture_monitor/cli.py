from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_DASHBOARD = "http://127.0.0.1:8765"


def run_server(args: argparse.Namespace) -> int:
    os.environ.setdefault("CAPTURE_HOST", args.host)
    os.environ.setdefault("CAPTURE_PORT", str(args.port))
    os.environ.setdefault("MONITOR_TARGET_URL", args.target_url)
    os.environ.setdefault("MONITOR_TARGET_HOST", args.target_url)
    if args.db_path:
        os.environ["CAPTURE_DB_PATH"] = args.db_path

    import uvicorn

    uvicorn.run(
        "packet_capture_monitor.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def run_proxy(args: argparse.Namespace) -> int:
    addon_path = Path(__file__).with_name("addon.py")
    env = os.environ.copy()
    env["MONITOR_API_URL"] = args.dashboard.rstrip("/")
    env["MONITOR_TARGET_URL"] = args.target_url
    env["MONITOR_TARGET_HOST"] = args.target_url
    env["MONITOR_INCLUDE_SUBDOMAINS"] = "1" if args.include_subdomains else "0"
    env["MONITOR_BODY_LIMIT"] = str(args.body_limit)
    proxy_auth = args.proxy_auth or os.environ.get("MITMPROXY_PROXY_AUTH", "")

    command = [
        "mitmdump",
        "--listen-host",
        args.listen_host,
        "--listen-port",
        str(args.listen_port),
        "-s",
        str(addon_path),
    ]
    if proxy_auth:
        command.extend(["--proxyauth", proxy_auth])

    sibling_mitmdump = Path(sys.executable).with_name("mitmdump")
    mitmdump_path = str(sibling_mitmdump) if sibling_mitmdump.exists() else shutil.which("mitmdump")
    if mitmdump_path:
        command[0] = mitmdump_path
        return subprocess.call(command, env=env)

    try:
        import mitmproxy.tools.main  # noqa: F401
    except ImportError:
        print(
            "mitmdump 未安装。请先执行 `pip install -e .`，"
            "或在当前虚拟环境中安装 mitmproxy。",
            file=sys.stderr,
        )
        return 2

    from mitmproxy.tools.main import mitmdump

    os.environ.update(env)
    sys.argv = command
    mitmdump()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packet-monitor",
        description="Local visual HTTP(S) request monitor for a configured domain.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    server = subparsers.add_parser("server", help="Start the dashboard API and UI.")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--target-url", "--target-host", default="https://ikuuu.win")
    server.add_argument("--db-path", default="")
    server.add_argument("--reload", action="store_true")
    server.set_defaults(func=run_server)

    proxy = subparsers.add_parser("proxy", help="Start the mitmproxy collector.")
    proxy.add_argument("--listen-host", default="127.0.0.1")
    proxy.add_argument("--listen-port", type=int, default=8081)
    proxy.add_argument("--dashboard", default=DEFAULT_DASHBOARD)
    proxy.add_argument("--target-url", "--target-host", default="https://ikuuu.win")
    proxy.add_argument("--body-limit", type=int, default=2 * 1024 * 1024)
    proxy.add_argument("--proxy-auth", default="", help='Proxy auth in "username:password" format.')
    proxy.add_argument(
        "--include-subdomains",
        dest="include_subdomains",
        action="store_true",
        default=True,
    )
    proxy.add_argument(
        "--no-include-subdomains",
        dest="include_subdomains",
        action="store_false",
    )
    proxy.set_defaults(func=run_proxy)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
