from __future__ import annotations

import json
import os
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class UserAccount:
    user_id: str
    username: str
    password: str
    proxy_username: str
    proxy_password: str
    default_target_url: str
    include_subdomains: bool


class UserDirectory:
    def __init__(self, users: Iterable[UserAccount]) -> None:
        accounts = list(users)
        if not accounts:
            raise ValueError("at least one dashboard user is required")

        self._by_id: Dict[str, UserAccount] = {}
        self._by_username: Dict[str, UserAccount] = {}
        self._by_proxy_username: Dict[str, UserAccount] = {}

        for account in accounts:
            if account.user_id in self._by_id:
                raise ValueError(f"duplicate user id: {account.user_id}")
            if account.username in self._by_username:
                raise ValueError(f"duplicate dashboard username: {account.username}")
            if account.proxy_username in self._by_proxy_username:
                raise ValueError(f"duplicate proxy username: {account.proxy_username}")
            self._by_id[account.user_id] = account
            self._by_username[account.username] = account
            self._by_proxy_username[account.proxy_username] = account

        self.default_user_id = accounts[0].user_id

    @classmethod
    def from_env(cls) -> "UserDirectory":
        raw = os.environ.get("DASHBOARD_USERS", "").strip()
        users_file = os.environ.get("DASHBOARD_USERS_FILE", "").strip()
        if users_file:
            raw = Path(users_file).read_text(encoding="utf-8").strip()

        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                records = []
                for username, values in payload.items():
                    record = dict(values or {})
                    record.setdefault("username", username)
                    records.append(record)
            elif isinstance(payload, list):
                records = payload
            else:
                raise ValueError("DASHBOARD_USERS must be a JSON array or object")
            return cls(cls._account_from_record(record) for record in records)

        username = os.environ.get("DASHBOARD_USERNAME", "admin")
        password = os.environ.get("DASHBOARD_PASSWORD", "")
        proxy_username = username
        proxy_password = password
        proxy_auth = os.environ.get("MITMPROXY_PROXY_AUTH", "")
        if ":" in proxy_auth:
            proxy_username, proxy_password = proxy_auth.split(":", 1)

        return cls(
            [
                UserAccount(
                    user_id=username,
                    username=username,
                    password=password,
                    proxy_username=proxy_username,
                    proxy_password=proxy_password,
                    default_target_url=os.environ.get("MONITOR_TARGET_URL")
                    or os.environ.get("MONITOR_TARGET_HOST")
                    or "https://www.aliyundrive.com/",
                    include_subdomains=os.environ.get("MONITOR_INCLUDE_SUBDOMAINS", "1") != "0",
                )
            ]
        )

    @staticmethod
    def _bool_value(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    @staticmethod
    def _account_from_record(record: Dict[str, Any]) -> UserAccount:
        username = str(record.get("username") or "").strip()
        if not username:
            raise ValueError("dashboard user username is required")
        password = str(record.get("password") or "")
        proxy_username = str(record.get("proxy_username") or username).strip()
        proxy_password = str(record.get("proxy_password") or password)
        target_url = str(record.get("target_url") or os.environ.get("MONITOR_TARGET_URL") or "https://www.aliyundrive.com/")
        include_subdomains = record.get("include_subdomains")
        if include_subdomains is None:
            include_subdomains = os.environ.get("MONITOR_INCLUDE_SUBDOMAINS", "1") != "0"

        return UserAccount(
            user_id=str(record.get("user_id") or username).strip(),
            username=username,
            password=password,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            default_target_url=target_url,
            include_subdomains=UserDirectory._bool_value(include_subdomains),
        )

    def auth_enabled(self) -> bool:
        return any(account.password for account in self._by_id.values())

    def get(self, user_id: str) -> Optional[UserAccount]:
        return self._by_id.get(user_id)

    def require(self, user_id: str) -> UserAccount:
        account = self.get(user_id)
        if not account:
            raise KeyError(user_id)
        return account

    def authenticate_dashboard(self, username: str, password: str) -> Optional[UserAccount]:
        account = self._by_username.get(username)
        if not account:
            return None
        if not hmac.compare_digest(account.password, password):
            return None
        return account

    def authenticate_proxy(self, username: str, password: str) -> Optional[UserAccount]:
        account = self._by_proxy_username.get(username)
        if not account:
            return None
        if not hmac.compare_digest(account.proxy_password, password):
            return None
        return account

    def public_user(self, account: UserAccount) -> Dict[str, Any]:
        return {
            "user_id": account.user_id,
            "username": account.username,
            "proxy_username": account.proxy_username,
        }
