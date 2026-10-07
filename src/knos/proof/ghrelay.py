from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from knos.common.exceptions import NetworkError, TimeoutError
from knos.common.relay import NetworkRelay
from knos.config import NodeConfig
from knos.config.constants import KNOS_DIR
from knos.config.env import is_testnet

from .errors import (
    GatewayTimeoutError,
    InternalError,
    NotReadyError,
    ServiceUnavailableError,
    TokenExpiredError,
)
from .stats import RelayStats

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r"(eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
SIGNATURE_PATTERN = re.compile(r"[a-f0-9]{64}")


class GitHubRelay(NetworkRelay):
    def __init__(self, config: NodeConfig, repo_owner: str, repo_name: str) -> None:
        self.config = config
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.stats = RelayStats(repo_owner=repo_owner, repo_name=repo_name)
        self._session: aiohttp.ClientSession | None = None
        self._token = ""
        self._token_expiry: float = 0
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_token(self) -> str:
        if self._token and self._token_expiry > asyncio.get_event_loop().time():
            return self._token

        async with self._lock:
            if self._token and self._token_expiry > asyncio.get_event_loop().time():
                return self._token

            session = await self._ensure_session()
            url = (
                f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/install"
            )
            async with session.post(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    self._token = data["token"]
                    self._token_expiry = data["expires_at"]
                    return self._token

                if resp.status == 403:
                    raise TokenExpiredError("GitHub API rate limited")
                if resp.status == 404:
                    raise NotReadyError("Repository not found")
                if resp.status == 503:
                    raise ServiceUnavailableError("GitHub API unavailable")
                if resp.status == 504:
                    raise GatewayTimeoutError("GitHub API timeout")
                resp.raise_for_status()

        raise InternalError(f"Unexpected status: {resp.status}")

    async def relay(
        self, tx_hash: str, signatures: list[str]
    ) -> dict[str, object]:
        if not signatures:
            raise ValueError("At least one signature required")

        token = await self.get_token()
        session = await self._ensure_session()

        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues/comments"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        }

        for signature in signatures:
            body = {
                "body": f"Relaying transaction: {tx_hash}\nSignature: {signature}"
            }
            async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 201:
                    self.stats.record(success=True, tx_hash=tx_hash, signature=signature)
                    return await resp.json()
                if resp.status == 403:
                    raise TokenExpiredError("GitHub API rate limited")
                if resp.status == 404:
                    raise NotReadyError("Repository not found")
                if resp.status == 500:
                    raise InternalError("GitHub API error")
                resp.raise_for_status()

        raise InternalError("Failed to relay all signatures")

    async def relay_messages(
        self, messages: list[dict[str, str]]
    ) -> dict[str, object]:
        if not messages:
            raise ValueError("At least one message required")

        token = await self.get_token()
        session = await self._ensure_session()

        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues/comments"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        }

        for message in messages:
            body = {"body": f"Message: {message.get('content', '')}\nFrom: {message.get('from', 'unknown')}"}
            async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 201:
                    self.stats.record(success=True, tx_hash=message.get("tx_hash", ""), signature=message.get("signature", ""))
                    return await resp.json()
                if resp.status == 403:
                    raise TokenExpiredError("GitHub API rate limited")
                if resp.status == 404:
                    raise NotReadyError("Repository not found")
                if resp.status == 500:
                    raise InternalError("GitHub API error")
                resp.raise_for_status()

        raise InternalError("Failed to relay all messages")

    async def _on_relayed_token(self, token_account: str, signature: str) -> None:
        self.stats.record(success=True, tx_hash="", signature=signature)
        logger.info("Relayed token %s sig=%s", token_account, signature)
        await self.stats.sync()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @classmethod
    async def create(cls, config: NodeConfig) -> GitHubRelay:
        if not is_testnet():
            raise ValueError("GitHubRelay only supports testnet")

        repo_name = config.relay_repo
        if not repo_name:
            raise ValueError("relay_repo not configured")

        owner, name = repo_name.split("/")
        return cls(config=config, repo_owner=owner, repo_name=name)
