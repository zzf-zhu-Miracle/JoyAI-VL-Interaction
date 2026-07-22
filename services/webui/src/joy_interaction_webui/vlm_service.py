# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
VLM Service
Pushes sampled video frames and user text into a Qwen-Omni-Realtime session.
Responses (text + audio) stream back through the Omni session callbacks wired
up in server.py; this class only tracks lightweight state/metrics.
"""

import asyncio
import io
import re
import time
from PIL import Image
from typing import Optional
import logging

from .omni_session import (
    OMNI_INSTRUCTIONS,
    OMNI_INSTRUCTIONS_NO_DELEGATION,
    OMNI_MODEL,
    OmniRealtimeSession,
)
from .proactive_watcher import ProactiveWatcher

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_DEFAULT_KEY = "DEFAULT_SYSTEM_PROMPT_EN"
SYSTEM_PROMPT_NO_DELEGATION_KEY = "DEFAULT_SYSTEM_PROMPT_NO_DELEGATION"
SYSTEM_PROMPT_OPTIONS = (
    {
        "key": SYSTEM_PROMPT_DEFAULT_KEY,
        "label": "Default (delegation)",
        "description": "DEFAULT_SYSTEM_PROMPT_EN",
    },
    {
        "key": SYSTEM_PROMPT_NO_DELEGATION_KEY,
        "label": "No delegation",
        "description": "DEFAULT_SYSTEM_PROMPT_NO_DELEGATION",
    },
)
_VALID_SYSTEM_PROMPT_KEYS = {option["key"] for option in SYSTEM_PROMPT_OPTIONS}

_INSTRUCTIONS_BY_KEY = {
    SYSTEM_PROMPT_DEFAULT_KEY: OMNI_INSTRUCTIONS,
    SYSTEM_PROMPT_NO_DELEGATION_KEY: OMNI_INSTRUCTIONS_NO_DELEGATION,
}


def _normalize_system_prompt_key(value: Optional[str]) -> str:
    key = str(value or SYSTEM_PROMPT_DEFAULT_KEY).strip()
    if key in _VALID_SYSTEM_PROMPT_KEYS:
        return key
    return SYSTEM_PROMPT_DEFAULT_KEY


class VLMService:
    """Omni-backed replacement for the old chat-completions VLM service."""

    is_omni = True

    def __init__(
        self,
        model: str,
        api_base: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        prompt: Optional[str] = None,
        max_tokens: int = 512,
        session_id: str = "default",
        system_prompt_key: str = SYSTEM_PROMPT_DEFAULT_KEY,
    ):
        self.model = model or OMNI_MODEL
        # Kept for server_config display/backwards compat only; the Omni
        # endpoint and credentials come from the environment.
        self.api_base = api_base
        self.api_key = api_key if api_key else "EMPTY"
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.session_id = session_id
        self.system_prompt_key = _normalize_system_prompt_key(system_prompt_key)
        self.omni = OmniRealtimeSession(
            session_id,
            model=self.model,
            instructions=_INSTRUCTIONS_BY_KEY[self.system_prompt_key],
            delegation_enabled=self.system_prompt_key != SYSTEM_PROMPT_NO_DELEGATION_KEY,
        )
        self.proactive_watcher = ProactiveWatcher(
            session_id,
            inject_callback=self._inject_proactive_alert,
        )
        self.current_response = "Initializing..."
        self.is_processing = False
        self._processing_lock = asyncio.Lock()
        self._active_tasks = set()
        self._closed = False
        self.last_latency_breakdown_ms = {}
        self.last_frame_timing_ms = {}
        self.last_user_prompt = ""
        self._last_background_handoff_meta: Optional[dict] = None

        # Metrics tracking
        self.last_inference_time = 0.0  # seconds
        self.total_inferences = 0
        self.total_inference_time = 0.0
        self._response_start_time: Optional[float] = None

    @staticmethod
    def system_prompt_options() -> list[dict[str, str]]:
        return [dict(option) for option in SYSTEM_PROMPT_OPTIONS]

    def update_system_prompt_key(self, key: Optional[str]) -> str:
        self.system_prompt_key = _normalize_system_prompt_key(key)
        logger.info(
            "Updated system prompt for session %s to: %s",
            self.session_id,
            self.system_prompt_key,
        )
        self.omni.instructions = _INSTRUCTIONS_BY_KEY[self.system_prompt_key]
        self.omni.delegation_enabled = (
            self.system_prompt_key != SYSTEM_PROMPT_NO_DELEGATION_KEY
        )
        try:
            asyncio.get_running_loop().create_task(
                self.omni.update_instructions(
                    self.omni.instructions,
                    delegation_enabled=self.omni.delegation_enabled,
                )
            )
        except RuntimeError:
            pass
        return self.system_prompt_key

    # ------------------------------------------------------------------
    # Frame ingestion
    # ------------------------------------------------------------------

    async def analyze_image(
        self,
        image: Image.Image,
        prompt: Optional[str] = None,
        frame_metadata: Optional[dict] = None,
    ) -> str:
        """JPEG-encode a frame and append it to the Omni session."""
        try:
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format="JPEG")
            jpeg_bytes = img_byte_arr.getvalue()
            self.proactive_watcher.start()  # idempotent; no-op when disabled
            self.proactive_watcher.submit_frame(jpeg_bytes)
            await self.omni.append_image(jpeg_bytes)
            return ""
        except Exception as e:
            logger.warning(f"Error sending frame to Omni session {self.session_id}: {e}")
            return ""

    async def _inject_proactive_alert(self, text: str) -> None:
        try:
            await self.omni.inject_proactive_alert(text)
        except Exception as e:
            logger.warning(
                f"Error injecting proactive alert into Omni session {self.session_id}: {e}"
            )

    async def process_frame(
        self,
        image: Image.Image,
        prompt: Optional[str] = None,
        frame_timing_ms: Optional[dict] = None,
        frame_metadata: Optional[dict] = None,
    ) -> None:
        if self._closed:
            return

        task = self._track_current_task()
        try:
            if self._processing_lock.locked():
                logger.debug("Omni busy, skipping frame")
                return

            async with self._processing_lock:
                if self._closed:
                    return
                self.last_frame_timing_ms = frame_timing_ms or {}
                await self.analyze_image(image, prompt, frame_metadata=frame_metadata)
        finally:
            self._untrack_task(task)

    async def process_frame_batch(
        self,
        frames_data: list,
        prompt: Optional[str] = None,
        frame_timing_ms: Optional[dict] = None,
    ) -> None:
        """Append each frame of a batch to the Omni session."""
        if self._closed:
            return

        task = self._track_current_task()
        try:
            if self._processing_lock.locked():
                logger.debug("Omni busy, skipping frame batch")
                return

            async with self._processing_lock:
                if self._closed:
                    return
                self.last_frame_timing_ms = frame_timing_ms or {}
                for frame_data in frames_data:
                    image = frame_data.get("image") if isinstance(frame_data, dict) else None
                    if image is not None:
                        await self.analyze_image(image, prompt)
        finally:
            self._untrack_task(task)

    # ------------------------------------------------------------------
    # User text
    # ------------------------------------------------------------------

    async def send_user_text(self, text: str) -> None:
        """Forward a typed prompt to the Omni session as a user message."""
        text = str(text or "").strip()
        if not text:
            return
        self.last_user_prompt = text
        try:
            await self.omni.send_user_text(text)
        except Exception as e:
            logger.warning(f"Error sending user text to Omni session {self.session_id}: {e}")

    def update_prompt(self, new_prompt: Optional[str]) -> None:
        prompt_text = new_prompt.strip() if new_prompt else None
        self.prompt = None
        if prompt_text:
            try:
                asyncio.get_running_loop().create_task(self.send_user_text(prompt_text))
            except RuntimeError:
                pass
        logger.info(f"Updated prompt to: {prompt_text}")

    # ------------------------------------------------------------------
    # Omni response bookkeeping (called from server.py callbacks)
    # ------------------------------------------------------------------

    def begin_omni_response(self) -> None:
        """Mark the start of a streamed Omni response."""
        if self._response_start_time is None:
            self._response_start_time = time.perf_counter()
            self.total_inferences += 1
        self.is_processing = True

    def update_omni_response(self, text: str, final: bool = False) -> None:
        self.current_response = text
        if final:
            if self._response_start_time is not None:
                self.last_inference_time = time.perf_counter() - self._response_start_time
                self.total_inference_time += self.last_inference_time
                self._response_start_time = None
            self.is_processing = False

    def queue_background_handoff(
        self,
        *,
        task_id: str,
        question: str,
        summary: str,
    ) -> None:
        """Record background completion metadata for UI/metrics."""
        compact_summary = self._compact_text(summary, 3500)
        self._last_background_handoff_meta = {
            "task_id": str(task_id or ""),
            "question": str(question or ""),
            "summary": compact_summary,
        }
        logger.info(
            "Recorded background handoff metadata for interaction session %s: task_id=%s summary_chars=%s",
            self.session_id,
            task_id,
            len(compact_summary),
        )

    def consume_background_handoff_metric(self) -> Optional[dict]:
        meta = self._last_background_handoff_meta
        self._last_background_handoff_meta = None
        return meta

    def _compact_text(self, text, limit: int) -> str:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 12)].rstrip() + " ...[截断]"

    # ------------------------------------------------------------------
    # State / metrics / lifecycle
    # ------------------------------------------------------------------

    def _track_current_task(self):
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
        return task

    def _untrack_task(self, task) -> None:
        if task is not None:
            self._active_tasks.discard(task)

    async def cancel_active_requests(self, timeout: float = 2.0) -> int:
        """Cancel in-flight frame processing tasks for this session."""
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in self._active_tasks
            if task is not current_task and not task.done()
        ]
        if not tasks:
            self.is_processing = False
            return 0

        for task in tasks:
            task.cancel()

        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        for task in pending:
            logger.warning(
                "Timed out waiting for VLM task cancellation for session %s",
                self.session_id,
            )

        self.is_processing = False
        logger.info("Cancelled %s VLM task(s) for session %s", len(tasks), self.session_id)
        return len(tasks)

    def clear_state(self) -> None:
        self.current_response = "Initializing..."
        self.is_processing = False
        self.prompt = None
        self.last_latency_breakdown_ms = {}
        self.last_frame_timing_ms = {}
        self.last_user_prompt = ""
        self._last_background_handoff_meta = None
        self.last_inference_time = 0.0
        self.total_inferences = 0
        self.total_inference_time = 0.0
        self._response_start_time = None

    async def close(self, cancel_requests: bool = True) -> None:
        self._closed = True
        if cancel_requests:
            await self.cancel_active_requests()
        self.clear_state()
        await self.proactive_watcher.stop()
        await self.omni.close()

    async def reset_conversation(self) -> bool:
        """Close the Omni connection; the next send starts a fresh conversation."""
        try:
            await self.proactive_watcher.stop()
            await self.omni.reset()
            return True
        except Exception as e:
            logger.warning(f"Omni reset failed for session {self.session_id}: {e}")
            return False

    def get_current_response(self) -> tuple[str, bool]:
        return self.current_response, self.is_processing

    def get_metrics(self) -> dict:
        avg_latency = (
            self.total_inference_time / self.total_inferences if self.total_inferences > 0 else 0.0
        )

        metrics = {
            "last_latency_ms": self.last_inference_time * 1000,
            "avg_latency_ms": avg_latency * 1000,
            "total_inferences": self.total_inferences,
            "is_processing": self.is_processing,
            "latency_breakdown_ms": self.last_latency_breakdown_ms,
            "frame_timing_ms": self.last_frame_timing_ms,
            "user_prompt": self.last_user_prompt,
            "proactive": self.proactive_watcher.stats(),
        }
        if self._last_background_handoff_meta:
            metrics["background_handoff"] = self._last_background_handoff_meta
        return metrics

    def get_last_request_payload(self) -> Optional[dict]:
        return None

    def get_last_response_payload(self) -> Optional[dict]:
        return None

    def update_api_settings(
        self, api_base: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        # Omni endpoint/credentials are env-configured; keep values for display.
        if api_base:
            self.api_base = api_base
        if api_key is not None:
            self.api_key = api_key if api_key else "EMPTY"
        logger.info(f"Updated API settings - base: {self.api_base} (Omni uses env config)")

    def update_model(self, new_model: Optional[str]) -> None:
        model = str(new_model or "").strip()
        if not model or model == self.model:
            return
        self.model = model
        self.omni.model = model
        try:
            asyncio.get_running_loop().create_task(self.omni.reset())
        except RuntimeError:
            pass
        logger.info(f"Updated Omni model for session {self.session_id} to: {model}")
