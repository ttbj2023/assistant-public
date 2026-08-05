"""url_safety SSRF 校验工具单元测试.

测试 src/tools/shared/url_safety.py:
- _is_blocked_addr: 纯 IP 分类 (loopback/private/link-local/metadata 等)
- is_safe_url: 协议白名单 + IP 字面量直查 + 域名 DNS 解析后二次校验
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

import src.tools.shared.url_safety as us


class TestIsBlockedAddr:
    """纯 IP 地址分类, 无网络依赖."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.255.255.255",
            "169.254.169.254",
            "10.0.0.1",
            "192.168.1.1",
            "172.16.0.1",
            "172.31.255.255",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "fc00::1",
        ],
    )
    def test_blocked_addresses(self, ip):
        addr = ipaddress.ip_address(ip)
        assert us._is_blocked_addr(addr) is True, f"{ip} 应被拦截"

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
        ],
    )
    def test_public_addresses(self, ip):
        addr = ipaddress.ip_address(ip)
        assert us._is_blocked_addr(addr) is False, f"{ip} 应为公网"


class TestIsSafeUrl:
    def test_public_url_safe(self, monkeypatch):
        """公网域名解析到公网 IP 应放行."""
        monkeypatch.setattr(
            us.socket,
            "getaddrinfo",
            lambda host, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, "", "", ("93.184.216.34", 0)),
            ],
        )
        ok, _ = us.is_safe_url("https://example.com/path?q=1")
        assert ok

    def test_loopback_ip_literal_blocked(self):
        ok, reason = us.is_safe_url("http://127.0.0.1/admin")
        assert not ok
        assert "禁止" in reason

    def test_metadata_endpoint_blocked(self):
        """云元数据端点 169.254.169.254 必须拦截."""
        ok, _ = us.is_safe_url("http://169.254.169.254/latest/meta-data/")
        assert not ok

    def test_private_ip_literal_blocked(self):
        ok, _ = us.is_safe_url("http://10.0.0.1/")
        assert not ok

    def test_bad_scheme_blocked(self):
        ok, reason = us.is_safe_url("file:///etc/passwd")
        assert not ok
        assert "协议" in reason

    def test_localhost_resolves_blocked(self, monkeypatch):
        """localhost 解析到回环地址应拦截."""
        monkeypatch.setattr(
            us.socket,
            "getaddrinfo",
            lambda host, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, "", "", ("127.0.0.1", 0)),
            ],
        )
        ok, _ = us.is_safe_url("http://localhost/")
        assert not ok

    def test_dns_rebinding_blocked(self, monkeypatch):
        """域名解析到内网 IP (DNS rebinding) 应拦截."""
        monkeypatch.setattr(
            us.socket,
            "getaddrinfo",
            lambda host, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, "", "", ("192.168.0.5", 0)),
            ],
        )
        ok, _ = us.is_safe_url("http://evil.example.com/")
        assert not ok

    def test_missing_hostname_blocked(self):
        ok, _ = us.is_safe_url("https://")
        assert not ok

    def test_resolution_failure_blocked(self, monkeypatch):
        def raise_gaierror(host, *a, **k):
            raise socket.gaierror("dns fail")

        monkeypatch.setattr(us.socket, "getaddrinfo", raise_gaierror)
        ok, _ = us.is_safe_url("https://nonexistent.invalid/")
        assert not ok
