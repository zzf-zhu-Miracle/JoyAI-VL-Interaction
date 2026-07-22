
"""Omni audio downstream for the browser.

The browser opens /api/tts once per session; server.py pushes pcm16 mono
24kHz chunks as binary frames whenever the Omni realtime API streams response
audio, plus a JSON {"type": "response.done"} at each response boundary.
A {"type": "stop"} text message from the browser cancels the in-flight Omni
response (barge-in).
"""

import asyncio
import json
import logging
import os

from aiohttp import web

# Audio parameters (Omni output format: pcm16 mono 24kHz)
TTS_SAMPLE_RATE = int(os.getenv("TTS_SAMPLE_RATE", "24000"))

logger = logging.getLogger(__name__)

# Wired up by setup_tts_routes(); provided by server.py.
_omni_resolver = None
_register_client = None
_unregister_client = None


async def tts_websocket_handler(request):
    client_ws = web.WebSocketResponse(heartbeat=20, max_msg_size=0)
    await client_ws.prepare(request)

    session_id = request.query.get("session_id", "").strip() or "default"
    omni = _omni_resolver(session_id) if _omni_resolver else None
    if omni is None:
        logger.warning("[%s] TTS websocket rejected: no Omni session resolver", session_id)
        await client_ws.close()
        return client_ws

    if _register_client is not None:
        _register_client(session_id, client_ws)
    logger.info("[%s] Browser TTS websocket connected", session_id)

    try:
        await client_ws.send_json(
            {
                "type": "start",
                "format": "pcm16",
                "sample_rate": TTS_SAMPLE_RATE,
                "channels": 1,
            }
        )
        async for msg in client_ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                message_type = data.get("type")
                if message_type == "stop":
                    asyncio.create_task(omni.cancel_response())
                elif message_type == "ping":
                    await client_ws.send_json({"type": "pong", "id": data.get("id")})
            elif msg.type == web.WSMsgType.ERROR:
                raise client_ws.exception() or RuntimeError("TTS client websocket error")
    except Exception as err:
        logger.warning("[%s] Browser TTS websocket failed: %s", session_id, err)
    finally:
        if _unregister_client is not None:
            _unregister_client(session_id, client_ws)
        if not client_ws.closed:
            await client_ws.close()
        logger.info("[%s] Browser TTS websocket closed", session_id)

    return client_ws


async def tts_config_handler(request):
    return web.json_response(
        {
            "sample_rate": TTS_SAMPLE_RATE,
            "format": "pcm16",
            "channels": 1,
        }
    )


def setup_tts_routes(app, omni_resolver=None, register_client=None, unregister_client=None):
    global _omni_resolver, _register_client, _unregister_client
    _omni_resolver = omni_resolver
    _register_client = register_client
    _unregister_client = unregister_client
    app.router.add_get("/api/tts/config", tts_config_handler)
    app.router.add_get("/api/tts", tts_websocket_handler)
