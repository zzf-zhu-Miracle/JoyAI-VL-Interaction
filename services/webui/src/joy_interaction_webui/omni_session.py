
"""Qwen-Omni-Realtime (Alibaba Bailian) websocket session manager.

Owns one persistent websocket connection to the Omni realtime API per browser
session. A single connection carries microphone audio in, video frames in, and
text+audio out, replacing the local VLM/ASR/TTS pipeline.

Configuration comes from the environment:
- DASHSCOPE_API_KEY     (required, never hardcoded)
- OMNI_REALTIME_URL     websocket endpoint (model is appended as a query param)
- OMNI_MODEL            realtime model name
- OMNI_VOICE            output voice
- OMNI_INSTRUCTIONS     system instructions for the assistant
- OMNI_TURN_DETECTION   semantic_vad | server_vad | none
"""

import asyncio
import base64
import io
import json
import logging
import os
import time

import websockets
from PIL import Image

logger = logging.getLogger(__name__)

OMNI_REALTIME_URL = os.environ.get(
    "OMNI_REALTIME_URL",
    "wss://llm-3ry3olqk3caoo2f0.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
)
OMNI_MODEL = os.environ.get("OMNI_MODEL", "qwen3.5-omni-flash-realtime")
OMNI_VOICE = os.environ.get("OMNI_VOICE", "Ethan")
OMNI_INSTRUCTIONS = os.environ.get(
    "OMNI_INSTRUCTIONS",
    (
        "你是一个实时多模态交互助手，可以通过摄像头画面和用户语音与用户交流。"
        "请始终使用中文、口语化且简洁地回答。"
        "当用户的问题或任务比较复杂（需要编写代码、深入分析、多步骤推理或联网检索）时，"
        "调用 delegate_to_agent 工具把问题委托给后台代理处理，并告诉用户稍等。"
    ),
)
OMNI_INSTRUCTIONS_NO_DELEGATION = os.environ.get(
    "OMNI_INSTRUCTIONS_NO_DELEGATION",
    (
        "你是一个实时多模态交互助手，可以通过摄像头画面和用户语音与用户交流。"
        "请始终使用中文、口语化且简洁地回答，所有问题都由你自己直接回答。"
    ),
)
OMNI_TURN_DETECTION = os.environ.get("OMNI_TURN_DETECTION", "semantic_vad")

OMNI_INPUT_SAMPLE_RATE = 16000  # pcm16 mono input
OMNI_OUTPUT_SAMPLE_RATE = 24000  # pcm16 mono output
OMNI_IMAGE_MAX_BYTES = 180 * 1024  # keep base64 payload below the 256KB limit
OMNI_CONNECT_TIMEOUT = float(os.environ.get("OMNI_CONNECT_TIMEOUT", "15"))
OMNI_RECONNECT_INITIAL_DELAY = 1.0
OMNI_RECONNECT_MAX_DELAY = 30.0

DELEGATE_TOOL_NAME = "delegate_to_agent"
DELEGATE_TOOL = {
    "type": "function",
    "name": DELEGATE_TOOL_NAME,
    "description": (
        "Delegate a complex task or question to the background coding agent, "
        "e.g. tasks that require writing code, deep analysis, multi-step "
        "reasoning or web search. The agent works asynchronously and the "
        "result is returned to you as a function call output."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The full question or task to delegate, in the user's language.",
            }
        },
        "required": ["question"],
    },
}


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


class OmniRealtimeSession:
    """One persistent websocket connection to the Omni realtime API.

    Async callbacks (all optional, awaited by the reader task):
    - on_input_transcript(phase, text, stash)   phase: "delta" | "completed"
    - on_output_transcript(phase, text)         phase: "delta" | "done"
    - on_audio(pcm_bytes)                       pcm16 mono 24kHz chunks
    - on_audio_done()                           response.audio.done
    - on_speech_started()                       server-side VAD detected user speech
    - on_response_done()                        response.done
    - on_function_call(call_id, name, arguments_json)
    - on_error(message)
    """

    def __init__(
        self,
        session_id: str,
        *,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        voice: str | None = None,
        instructions: str | None = None,
        turn_detection: str | None = None,
        delegation_enabled: bool = True,
    ):
        self.session_id = session_id
        self.url = (url or OMNI_REALTIME_URL).strip()
        self.model = (model or OMNI_MODEL).strip()
        self.api_key = api_key if api_key is not None else os.environ.get("DASHSCOPE_API_KEY", "")
        self.voice = (voice or OMNI_VOICE).strip()
        self.instructions = instructions if instructions is not None else OMNI_INSTRUCTIONS
        self.turn_detection = (turn_detection or OMNI_TURN_DETECTION).strip().lower()
        self.delegation_enabled = delegation_enabled

        self.on_input_transcript = None
        self.on_output_transcript = None
        self.on_audio = None
        self.on_audio_done = None
        self.on_speech_started = None
        self.on_response_done = None
        self.on_function_call = None
        self.on_error = None

        self.last_input_transcript = ""

        self._ws = None
        self._reader_task = None
        self._reconnect_task = None
        self._send_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._closed = False
        self._audio_sent = False
        self._connect_failures = 0
        self._connect_backoff_until = 0.0
        self._response_in_progress = False
        self._response_idle = asyncio.Event()
        self._response_idle.set()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _build_url(self) -> str:
        if "model=" in self.url:
            return self.url
        separator = "&" if "?" in self.url else "?"
        return f"{self.url}{separator}model={self.model}"

    def _session_update_payload(self) -> dict:
        if self.turn_detection in {"none", "off", "null", ""}:
            turn_detection = None
        else:
            turn_detection = {"type": self.turn_detection}
        session = {
            "modalities": ["text", "audio"],
            "voice": self.voice,
            "instructions": self.instructions,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "turn_detection": turn_detection,
            "input_audio_transcription": {"model": "qwen3-asr-flash-realtime"},
        }
        if self.delegation_enabled:
            session["tools"] = [DELEGATE_TOOL]
        return {"type": "session.update", "session": session}

    async def _connect(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is not set; cannot connect to the Omni realtime API"
            )
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = self._build_url()
        logger.info("[%s] Connecting to Omni realtime API: %s", self.session_id, url)
        try:
            ws = await websockets.connect(
                url,
                additional_headers=headers,
                open_timeout=OMNI_CONNECT_TIMEOUT,
                ping_interval=20,
                max_size=None,
            )
        except TypeError:
            # websockets < 12 used extra_headers instead of additional_headers
            ws = await websockets.connect(
                url,
                extra_headers=headers,
                open_timeout=OMNI_CONNECT_TIMEOUT,
                ping_interval=20,
                max_size=None,
            )
        self._ws = ws
        self._audio_sent = False
        self._reader_task = asyncio.create_task(
            self._reader_loop(ws), name=f"omni-reader:{self.session_id}"
        )
        await self._send(self._session_update_payload())
        # The API requires at least one audio chunk before images are accepted;
        # send a short silence primer so video frames flow even before the mic opens.
        primer = bytes(int(OMNI_INPUT_SAMPLE_RATE * 2 * 0.1))
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(primer).decode("ascii"),
            }
        )
        self._audio_sent = True
        self._connect_failures = 0
        self._connect_backoff_until = 0.0
        logger.info("[%s] Omni realtime session established", self.session_id)

    async def ensure_connected(self) -> None:
        """Connect on first use; reconnect after unexpected drops (with backoff)."""
        if self._closed:
            raise RuntimeError("Omni session is closed")
        if self._ws is not None:
            return
        async with self._connect_lock:
            if self._closed or self._ws is not None:
                if self._closed:
                    raise RuntimeError("Omni session is closed")
                return
            now = time.monotonic()
            if now < self._connect_backoff_until:
                raise RuntimeError("Omni reconnect backoff in progress")
            try:
                await self._connect()
            except Exception as err:
                self._connect_failures += 1
                delay = min(
                    OMNI_RECONNECT_INITIAL_DELAY * (2 ** (self._connect_failures - 1)),
                    OMNI_RECONNECT_MAX_DELAY,
                )
                self._connect_backoff_until = time.monotonic() + delay
                await self._emit_error(f"Omni connect failed: {err}")
                raise

    def _schedule_reconnect(self) -> None:
        if self._closed or self._reconnect_task is not None:
            return

        async def reconnect():
            try:
                while not self._closed and self._ws is None:
                    try:
                        await self.ensure_connected()
                    except Exception:
                        # Backoff state is updated inside ensure_connected; wait it out.
                        await asyncio.sleep(1.0)
            finally:
                self._reconnect_task = None

        self._reconnect_task = asyncio.create_task(
            reconnect(), name=f"omni-reconnect:{self.session_id}"
        )

    async def _reader_loop(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    logger.debug("[%s] Omni non-JSON frame ignored", self.session_id)
                    continue
                try:
                    await self._dispatch(event)
                except Exception:
                    logger.warning(
                        "[%s] Omni event dispatch failed: %s",
                        self.session_id,
                        event.get("type"),
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as err:
            logger.warning("[%s] Omni connection closed: %s", self.session_id, err)
        except Exception:
            logger.warning("[%s] Omni reader failed", self.session_id, exc_info=True)
        finally:
            if ws is self._ws:
                self._ws = None
                if not self._closed:
                    await self._emit_error("Omni connection lost; reconnecting")
                    self._schedule_reconnect()

    async def _dispatch(self, event: dict) -> None:
        event_type = event.get("type") or ""
        if event_type == "input_audio_buffer.speech_started":
            if self.on_speech_started:
                await self.on_speech_started()
        elif event_type == "conversation.item.input_audio_transcription.delta":
            if self.on_input_transcript:
                await self.on_input_transcript(
                    "delta", event.get("text") or "", event.get("stash") or ""
                )
        elif event_type == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript") or ""
            self.last_input_transcript = transcript
            if self.on_input_transcript:
                await self.on_input_transcript("completed", transcript, "")
        elif event_type == "response.audio_transcript.delta":
            if self.on_output_transcript:
                await self.on_output_transcript("delta", event.get("delta") or "")
        elif event_type == "response.audio_transcript.done":
            if self.on_output_transcript:
                await self.on_output_transcript("done", event.get("transcript") or "")
        elif event_type == "response.audio.delta":
            audio_b64 = event.get("delta") or ""
            if audio_b64 and self.on_audio:
                await self.on_audio(base64.b64decode(audio_b64))
        elif event_type == "response.audio.done":
            if self.on_audio_done:
                await self.on_audio_done()
        elif event_type == "response.function_call_arguments.done":
            if self.on_function_call:
                await self.on_function_call(
                    event.get("call_id") or "",
                    event.get("name") or "",
                    event.get("arguments") or "",
                )
        elif event_type == "response.created":
            self._response_in_progress = True
            self._response_idle.clear()
        elif event_type == "response.done":
            self._response_in_progress = False
            self._response_idle.set()
            if self.on_response_done:
                await self.on_response_done()
        elif event_type == "response.cancelled":
            self._response_in_progress = False
            self._response_idle.set()
        elif event_type == "error":
            self._response_in_progress = False
            self._response_idle.set()
            message = json.dumps(event.get("error") or event, ensure_ascii=False)
            logger.error("[%s] Omni API error: %s", self.session_id, message)
            await self._emit_error(message)
        else:
            logger.debug("[%s] Omni event: %s", self.session_id, event_type)

    async def _emit_error(self, message: str) -> None:
        if not self.on_error:
            return
        try:
            await self.on_error(message)
        except Exception:
            logger.debug("[%s] Omni on_error callback failed", self.session_id, exc_info=True)

    async def _send(self, event: dict) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("Omni session is not connected")
        async with self._send_lock:
            await ws.send(json.dumps(event, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def append_audio(self, pcm16_bytes: bytes) -> None:
        """Stream pcm16 mono 16kHz audio into the input buffer."""
        if not pcm16_bytes:
            return
        await self.ensure_connected()
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16_bytes).decode("ascii"),
            }
        )
        self._audio_sent = True

    async def append_image(self, jpeg_bytes: bytes) -> bool:
        """Append one JPEG frame. Re-encodes/downscales when above the size cap."""
        if not jpeg_bytes:
            return False
        await self.ensure_connected()
        if not self._audio_sent:
            logger.debug("[%s] Omni image skipped: no audio sent yet", self.session_id)
            return False
        if len(jpeg_bytes) > OMNI_IMAGE_MAX_BYTES:
            jpeg_bytes = self._reencode_image(jpeg_bytes)
        await self._send(
            {
                "type": "input_image_buffer.append",
                "image": base64.b64encode(jpeg_bytes).decode("ascii"),
            }
        )
        return True

    def _reencode_image(self, jpeg_bytes: bytes) -> bytes:
        try:
            image = Image.open(io.BytesIO(jpeg_bytes))
            if image.mode != "RGB":
                image = image.convert("RGB")
            for quality in (80, 60, 40):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                if buffer.tell() <= OMNI_IMAGE_MAX_BYTES:
                    return buffer.getvalue()
            while min(image.size) > 240:
                image = image.resize(
                    (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
                    getattr(getattr(Image, "Resampling", Image), "LANCZOS"),
                )
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=60, optimize=True)
                if buffer.tell() <= OMNI_IMAGE_MAX_BYTES:
                    return buffer.getvalue()
            return buffer.getvalue()
        except Exception:
            logger.warning("[%s] Omni image re-encode failed", self.session_id, exc_info=True)
            return jpeg_bytes

    async def send_user_text(self, text: str) -> bool:
        """Inject a typed user message and ask the model to respond.

        Returns False when the text duplicates the latest input transcript
        (the model already heard it as audio), to avoid double answers.
        """
        text = str(text or "").strip()
        if not text:
            return False
        if _normalize_text(text) and _normalize_text(text) == _normalize_text(
            self.last_input_transcript
        ):
            logger.info(
                "[%s] Omni text injection skipped (matches live transcript): %s",
                self.session_id,
                text[:80],
            )
            return False
        await self.ensure_connected()
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._send({"type": "response.create"})
        return True

    async def inject_proactive_alert(self, text: str) -> bool:
        """Inject a proactive watcher alert as a user message for the model to speak.

        Bypasses the transcript-echo dedup guard in send_user_text and waits
        for any in-flight response to finish (briefly) to avoid a
        response.create collision.
        """
        text = str(text or "").strip()
        if not text:
            return False
        await self.ensure_connected()
        if self._response_in_progress:
            try:
                await asyncio.wait_for(self._response_idle.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[%s] Proactive alert: in-flight response did not finish in time; injecting anyway",
                    self.session_id,
                )
        wrapped = (
            f"【监控提醒】{text}"
            "（请用口语自然地把这条重要提醒告知用户，不要提及“监控提醒”四个字）"
        )
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": wrapped}],
                },
            }
        )
        await self._send({"type": "response.create"})
        return True

    async def send_function_output(self, call_id: str, output: str) -> None:
        """Return a function call result to the model and let it speak the outcome."""
        if not call_id:
            return
        await self.ensure_connected()
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(output or ""),
                },
            }
        )
        await self._send({"type": "response.create"})

    async def cancel_response(self) -> None:
        """Cancel the in-flight response (barge-in from the browser)."""
        if self._ws is None:
            return
        try:
            await self._send({"type": "response.cancel"})
        except Exception:
            logger.debug("[%s] Omni response.cancel failed", self.session_id, exc_info=True)

    async def update_instructions(
        self, instructions: str, delegation_enabled: bool | None = None
    ) -> None:
        """Apply new instructions (and tool set) on the live connection."""
        self.instructions = instructions
        if delegation_enabled is not None:
            self.delegation_enabled = delegation_enabled
        if self._ws is None:
            return
        await self._send(self._session_update_payload())

    async def reset(self) -> None:
        """Close the connection; the next send starts a fresh conversation."""
        await self._close_connection()
        self.last_input_transcript = ""
        logger.info("[%s] Omni conversation reset", self.session_id)

    async def _close_connection(self) -> None:
        ws = self._ws
        self._ws = None
        reader = self._reader_task
        self._reader_task = None
        self._response_in_progress = False
        self._response_idle.set()
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("[%s] Omni close failed", self.session_id, exc_info=True)
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass

    async def close(self) -> None:
        self._closed = True
        reconnect = self._reconnect_task
        self._reconnect_task = None
        if reconnect is not None and reconnect is not asyncio.current_task():
            reconnect.cancel()
            try:
                await reconnect
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_connection()
