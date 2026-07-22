
"""Proactive watcher (主动播报): periodically analyzes the latest camera frame
with the Bailian OpenAI-compatible chat completions API and injects noteworthy
alerts into the foreground Omni realtime session.

A plain chat-completions endpoint is used (not a second realtime session)
because realtime sessions limit the number of video rounds.

Configuration comes from the environment (see module constants); the API key
is always read from DASHSCOPE_API_KEY and never hardcoded.
"""

import asyncio
import base64
import logging
import os
import time
from typing import Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

PROACTIVE_ENABLED = os.environ.get("PROACTIVE_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
PROACTIVE_API_BASE = os.environ.get(
    "PROACTIVE_API_BASE",
    "https://llm-3ry3olqk3caoo2f0.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)
PROACTIVE_MODEL = os.environ.get("PROACTIVE_MODEL", "qwen3.5-omni-flash")
PROACTIVE_INTERVAL_S = float(os.environ.get("PROACTIVE_INTERVAL_S", "3.0"))
PROACTIVE_MAX_TOKENS = int(os.environ.get("PROACTIVE_MAX_TOKENS", "128"))
PROACTIVE_TEMPERATURE = float(os.environ.get("PROACTIVE_TEMPERATURE", "0.2"))
PROACTIVE_COOLDOWN_S = float(os.environ.get("PROACTIVE_COOLDOWN_S", "15"))
PROACTIVE_TIMEOUT_S = float(os.environ.get("PROACTIVE_TIMEOUT_S", "10"))
PROACTIVE_SYSTEM_PROMPT = os.environ.get(
    "PROACTIVE_SYSTEM_PROMPT",
    (
        "你是一个视频监控助手，正在观看一路实时摄像头画面。"
        "当画面中出现值得用户立即注意的重要或异常情况（例如火灾、有人摔倒、陌生人闯入、"
        "危险动作，或其他用户很可能关心的事情）时，用一句简洁的中文提醒用户。"
        "如果画面一切正常、没有值得注意的事情，或者当前情况与你上一条提醒相比没有变化，"
        "请只输出 </silence>，不要输出任何其他内容。"
    ),
)

_SILENCE_MARKERS = ("</silence>", "<silence>", "< silence >", "< /silence >")


def _is_silence_output(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return True
    return any(marker in value for marker in _SILENCE_MARKERS)


class ProactiveWatcher:
    """Watches the newest frame and calls inject_callback on noteworthy events.

    Keeps only the latest JPEG frame (no queue); analysis errors are logged
    and the loop continues — the watcher never raises into its caller.
    """

    def __init__(
        self,
        session_id: str,
        *,
        inject_callback: Callable[[str], Awaitable[None]],
        api_base: str = PROACTIVE_API_BASE,
        model: str = PROACTIVE_MODEL,
        interval_s: float = PROACTIVE_INTERVAL_S,
        max_tokens: int = PROACTIVE_MAX_TOKENS,
        cooldown_s: float = PROACTIVE_COOLDOWN_S,
        enabled: Optional[bool] = None,
        api_key: Optional[str] = None,
    ):
        self.session_id = session_id
        self.inject_callback = inject_callback
        self.api_base = str(api_base or PROACTIVE_API_BASE).rstrip("/")
        self.model = str(model or PROACTIVE_MODEL)
        self.interval_s = max(0.5, float(interval_s))
        self.max_tokens = max(16, int(max_tokens))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.enabled = PROACTIVE_ENABLED if enabled is None else bool(enabled)
        self.api_key = api_key if api_key is not None else os.environ.get("DASHSCOPE_API_KEY", "")

        self._latest_frame: Optional[bytes] = None
        self._latest_frame_time = 0.0
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._stopped = False
        self._last_alert_text = ""
        self._last_alert_time = 0.0

        # Stats
        self.checks = 0
        self.alerts = 0
        self.errors = 0

    def start(self) -> None:
        """Start the watch loop (idempotent)."""
        if not self.enabled:
            return
        if self._task is not None:
            return
        if not self.api_key:
            logger.warning(
                "[%s] Proactive watcher disabled: DASHSCOPE_API_KEY is not set",
                self.session_id,
            )
            return
        self._stopped = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(PROACTIVE_TIMEOUT_S, connect=5.0)
        )
        self._task = asyncio.create_task(
            self._loop(), name=f"proactive-watcher:{self.session_id}"
        )
        logger.info(
            "[%s] Proactive watcher started: model=%s interval=%.1fs cooldown=%.1fs",
            self.session_id,
            self.model,
            self.interval_s,
            self.cooldown_s,
        )

    def submit_frame(self, jpeg_bytes: bytes) -> None:
        """Hand over the newest frame; older ones are dropped."""
        if jpeg_bytes:
            self._latest_frame = bytes(jpeg_bytes)
            self._latest_frame_time = time.monotonic()

    def stats(self) -> dict:
        return {"checks": self.checks, "alerts": self.alerts, "errors": self.errors}

    async def stop(self) -> None:
        """Stop the loop and reset dedup/cooldown state."""
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.debug("[%s] Proactive client close failed", self.session_id, exc_info=True)
            self._client = None
        self._last_alert_text = ""
        self._last_alert_time = 0.0

    async def _loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(self.interval_s)
                try:
                    await self._check_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.errors += 1
                    logger.warning(
                        "[%s] Proactive check failed", self.session_id, exc_info=True
                    )
        except asyncio.CancelledError:
            raise

    def _build_user_text(self) -> str:
        text = "这是当前摄像头画面，请判断是否需要提醒用户。"
        if self._last_alert_text:
            text += (
                f"\n你上一条提醒是：「{self._last_alert_text}」。"
                "如果当前情况与这条提醒相比没有变化，请保持沉默（只输出 </silence>）。"
            )
        return text

    async def _check_once(self) -> None:
        frame = self._latest_frame
        if not frame or self._client is None:
            return
        self.checks += 1
        image_url = f"data:image/jpeg;base64,{base64.b64encode(frame).decode('ascii')}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PROACTIVE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": self._build_user_text()},
                    ],
                },
            ],
            "max_tokens": self.max_tokens,
            "temperature": PROACTIVE_TEMPERATURE,
        }
        response = await self._client.post(
            f"{self.api_base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        text = self._extract_response_text(response.json())
        if _is_silence_output(text):
            logger.debug("[%s] Proactive check: silence", self.session_id)
            return

        alert = " ".join(str(text).split()).strip()
        now = time.monotonic()
        if now - self._last_alert_time < self.cooldown_s:
            logger.info(
                "[%s] Proactive alert suppressed by cooldown (%.0fs left): %s",
                self.session_id,
                self.cooldown_s - (now - self._last_alert_time),
                alert[:120],
            )
            return
        self._last_alert_time = now
        self._last_alert_text = alert
        self.alerts += 1
        logger.info("[%s] Proactive alert: %s", self.session_id, alert[:200])
        try:
            await self.inject_callback(alert)
        except Exception:
            logger.warning(
                "[%s] Proactive alert injection failed", self.session_id, exc_info=True
            )

    def _extract_response_text(self, data) -> str:
        try:
            message = data["choices"][0]["message"]
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = [
                    str(part.get("text") or "").strip()
                    for part in content
                    if isinstance(part, dict)
                ]
                return "\n".join(part for part in parts if part).strip()
            reasoning = message.get("reasoning_content")
            return str(reasoning or "").strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("[%s] Proactive: unexpected API response shape", self.session_id)
            return ""
