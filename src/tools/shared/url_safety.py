"""URL 安全校验工具 - 防 SSRF.

供 web_fetch / zhipu_reader / video_generation 等抓取外部 URL 的工具复用:
- 仅允许 http/https 协议
- 拦截回环 / 私网 / 链路本地(含云元数据 169.254.169.254) / 未指定 / 多播 / 保留地址
- 域名先 DNS 解析再校验解析结果, 防 DNS rebinding
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _is_blocked_addr(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """地址是否属于禁止访问的范围."""
    return bool(
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_private,
    )


def is_safe_url(url: str) -> tuple[bool, str]:
    """校验 URL 是否可安全抓取.

    Args:
        url: 待校验的完整 URL

    Returns:
        (是否安全, 原因). 安全时原因为 "ok", 否则为中文说明.

    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 解析失败"

    if parsed.scheme not in ("http", "https"):
        return False, f"不允许的协议: {parsed.scheme or '空'}"

    host = parsed.hostname
    if not host:
        return False, "缺少 hostname"

    addresses = _resolve_addresses(host)
    if not addresses:
        return False, f"无法解析主机: {host}"

    for addr in addresses:
        if _is_blocked_addr(addr):
            return False, f"目标地址被禁止: {addr}"

    return True, "ok"


def _resolve_addresses(
    host: str,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """解析主机名为 IP 地址列表.

    IP 字面量直接转换; 域名经 DNS 解析. 解析失败返回空列表.
    """
    ip_literal = _try_parse_ip(host)
    if ip_literal is not None:
        return [ip_literal]

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []

    results: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        ip = info[4][0]
        parsed = _try_parse_ip(ip)
        if parsed is not None:
            results.append(parsed)
    return results


def _try_parse_ip(
    value: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """尝试解析 IP 字面量, 失败返回 None."""
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


__all__ = ["_is_blocked_addr", "is_safe_url"]
