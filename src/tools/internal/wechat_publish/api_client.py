"""微信公众号API客户端 - 精简版.

从 wechat_pusher 项目迁移, 仅保留核心功能:
- Token 获取 + 缓存
- 永久素材上传
- 草稿创建
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time

import httpx

logger = logging.getLogger(__name__)


class WechatApiClient:
    """微信公众号API客户端."""

    def __init__(self, appid: str, secret: str) -> None:
        self.appid = appid
        self.secret = secret
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.appid,
            "secret": self.secret,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        if "access_token" in data:
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data["expires_in"] - 300
            logger.debug("微信 access_token 获取成功")
            assert self._access_token is not None
            return self._access_token

        errmsg = data.get("errmsg", "unknown")
        errcode = data.get("errcode", "unknown")
        raise RuntimeError(f"获取 access_token 失败: {errcode} - {errmsg}")

    async def upload_media(self, file_path: str, media_type: str = "image") -> dict:
        """上传永久素材, 返回 {"media_id": "...", "url": "..."}."""
        access_token = await self._get_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={access_token}&type={media_type}"

        async with httpx.AsyncClient(timeout=60) as client:
            media_bytes = await asyncio.to_thread(pathlib.Path(file_path).read_bytes)
            files = {
                "media": (
                    file_path.rsplit("/", maxsplit=1)[-1],
                    media_bytes,
                    "image/png",
                )
            }
            response = await client.post(url, files=files)
            response.raise_for_status()
            data = response.json()

        if "media_id" in data:
            return {"media_id": data["media_id"], "url": data.get("url", "")}

        errmsg = data.get("errmsg", "unknown")
        errcode = data.get("errcode", "unknown")
        raise RuntimeError(f"上传素材失败: {errcode} - {errmsg}")

    async def upload_news_draft(
        self,
        articles: list[dict],
    ) -> str:
        """创建草稿, 返回 draft media_id."""
        access_token = await self._get_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"

        payload = {"articles": articles}
        json_data = json.dumps(payload, ensure_ascii=False)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                content=json_data.encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            response.raise_for_status()
            data = response.json()

        if "media_id" in data:
            logger.info("微信草稿创建成功: %s", data["media_id"])
            return data["media_id"]

        errmsg = data.get("errmsg", "unknown")
        errcode = data.get("errcode", "unknown")
        raise RuntimeError(f"创建草稿失败: {errcode} - {errmsg}")


__all__ = ["WechatApiClient"]
