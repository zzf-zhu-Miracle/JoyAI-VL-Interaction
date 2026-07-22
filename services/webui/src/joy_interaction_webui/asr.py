
"""ASR websocket bridge: browser microphone audio -> Qwen-Omni-Realtime.

The browser protocol is unchanged: binary pcm16 mono 16kHz in, JSON result
events out (IS_PARTIAL / IS_FINAL). The bytes are forwarded into the session's
Omni realtime connection, and the Omni input transcription events are mapped
back onto the legacy result shape by server.py.
"""

import asyncio
import json
import logging
import os
import time
import uuid

from aiohttp import web

# ASR parameters
ASR_SAMPLE_RATE = int(os.getenv("ASR_SAMPLE_RATE", "16000"))
ASR_FINAL_TIMEOUT = float(os.getenv("ASR_FINAL_TIMEOUT", "8.0"))

logger = logging.getLogger(__name__)

# Wired up by setup_asr_routes(); provided by server.py.
_omni_resolver = None
_register_client = None
_unregister_client = None


async def send_asr_client_json(client_ws, payload):
    if not client_ws.closed:
        await client_ws.send_str(json.dumps(payload, ensure_ascii=False))


async def _wait_for_final_transcript(state):
    """Give the Omni VAD a moment to emit the final transcript after 'end'."""
    if state is None:
        return
    deadline = time.monotonic() + ASR_FINAL_TIMEOUT
    while time.monotonic() < deadline:
        if state.get("saw_completed"):
            return
        await asyncio.sleep(0.1)


async def asr_websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session_id = request.query.get("session_id", "").strip() or uuid.uuid4().hex[:8]
    state = None
    logger.info("[%s] Browser ASR websocket connected", session_id)

    try:
        omni = _omni_resolver(session_id) if _omni_resolver else None
        if omni is None:
            raise RuntimeError("Omni session resolver is not configured")
        await omni.ensure_connected()
        if _register_client is not None:
            state = _register_client(session_id, ws)
        await send_asr_client_json(
            ws,
            {"type": "status", "message": "connected", "sample_rate": ASR_SAMPLE_RATE},
        )

        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                try:
                    await omni.append_audio(msg.data)
                except Exception as err:
                    logger.warning("[%s] Omni audio append failed: %s", session_id, err)
            elif msg.type == web.WSMsgType.TEXT:
                try:
                    control = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if control.get("type") == "ping":
                    await send_asr_client_json(
                        ws,
                        {
                            "type": "pong",
                            "id": control.get("id"),
                            "client_ts": control.get("client_ts"),
                            "server_ts": time.time(),
                        },
                    )
                elif control.get("type") in {"end", "segment_end"}:
                    await _wait_for_final_transcript(state)
                    return
            elif msg.type in {web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED}:
                break
            elif msg.type == web.WSMsgType.ERROR:
                raise ws.exception() or RuntimeError("ASR client websocket error")
    except Exception as err:
        logger.exception("[%s] ASR websocket failed", session_id)
        try:
            await send_asr_client_json(ws, {"type": "error", "message": f"ASR failed: {err}"})
        except Exception:
            pass
    finally:
        if state is not None and _unregister_client is not None:
            _unregister_client(session_id, ws)
        if not ws.closed:
            await ws.close()
        logger.info("[%s] Browser ASR websocket closed", session_id)

    return ws


def setup_asr_routes(app, omni_resolver=None, register_client=None, unregister_client=None):
    global _omni_resolver, _register_client, _unregister_client
    _omni_resolver = omni_resolver
    _register_client = register_client
    _unregister_client = unregister_client
    app.router.add_get("/ws/asr", asr_websocket_handler)
