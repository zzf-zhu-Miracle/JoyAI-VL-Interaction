#!/usr/bin/env python3
"""Standalone sanity check for the Bailian Qwen-Omni-Realtime websocket API.

Connects with DASHSCOPE_API_KEY from the environment, sends session.update,
streams a few seconds of pcm16 16kHz audio (a wav file passed as argument, or
generated silence+sine), optionally appends one JPEG frame, and prints every
event received from the server.

Usage:
    python omni_smoke_test.py [--wav speech.wav] [--image frame.jpg] [--seconds 3]

Requires: pip install websockets
"""

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import struct
import sys
import time
import wave

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("omni_smoke_test")

OMNI_REALTIME_URL = os.environ.get(
    "OMNI_REALTIME_URL",
    "wss://llm-3ry3olqk3caoo2f0.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
)
OMNI_MODEL = os.environ.get("OMNI_MODEL", "qwen3.5-omni-flash-realtime")
OMNI_VOICE = os.environ.get("OMNI_VOICE", "Ethan")
OMNI_TURN_DETECTION = os.environ.get("OMNI_TURN_DETECTION", "semantic_vad")
SAMPLE_RATE = 16000
CHUNK_MS = 40


def load_wav_pcm16(path: str) -> bytes:
    """Read a wav file as pcm16 mono 16kHz (converts sample width/channels only)."""
    with wave.open(path, "rb") as wav_file:
        rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: only 16-bit PCM wav files are supported (got {width * 8}-bit)")
    if channels == 2:
        # Downmix stereo to mono by averaging channel pairs.
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        frames = struct.pack(
            f"<{len(samples) // 2}h",
            *((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)),
        )
    elif channels != 1:
        raise SystemExit(f"{path}: unsupported channel count {channels}")
    if rate != SAMPLE_RATE:
        logger.warning("%s: sample rate %s != %s; sending as-is (pitch will shift)", path, rate, SAMPLE_RATE)
    return frames


def generate_sine_pcm16(seconds: float, freq: float = 440.0) -> bytes:
    """Generate `seconds` of pcm16 mono 16kHz: 0.3s silence, sine tone, 0.3s silence."""
    total = int(SAMPLE_RATE * seconds)
    samples = []
    for i in range(total):
        t = i / SAMPLE_RATE
        if t < 0.3 or t > seconds - 0.3:
            samples.append(0)
        else:
            samples.append(int(12000 * math.sin(2 * math.pi * freq * t)))
    return struct.pack(f"<{len(samples)}h", *samples)


def build_url() -> str:
    if "model=" in OMNI_REALTIME_URL:
        return OMNI_REALTIME_URL
    separator = "&" if "?" in OMNI_REALTIME_URL else "?"
    return f"{OMNI_REALTIME_URL}{separator}model={OMNI_MODEL}"


async def run_smoke_test(wav_path: str | None, image_path: str | None, seconds: float) -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY is not set; export it before running this script")

    if wav_path:
        pcm = load_wav_pcm16(wav_path)
    else:
        pcm = generate_sine_pcm16(seconds)
    chunk_bytes = int(SAMPLE_RATE * 2 * CHUNK_MS / 1000)
    logger.info(
        "Audio prepared: %.2fs (%s bytes, %s chunks of %sms)",
        len(pcm) / (SAMPLE_RATE * 2),
        len(pcm),
        (len(pcm) + chunk_bytes - 1) // chunk_bytes,
        CHUNK_MS,
    )

    image_b64 = None
    if image_path:
        with open(image_path, "rb") as image_file:
            image_b64 = base64.b64encode(image_file.read()).decode("ascii")
        logger.info("Image prepared: %s (%s base64 chars)", image_path, len(image_b64))

    url = build_url()
    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info("Connecting to %s", url)
    try:
        ws = await websockets.connect(
            url, additional_headers=headers, open_timeout=15, ping_interval=20, max_size=None
        )
    except TypeError:
        ws = await websockets.connect(
            url, extra_headers=headers, open_timeout=15, ping_interval=20, max_size=None
        )

    audio_seconds = 0.0
    response_done = asyncio.Event()

    async def send_events() -> None:
        nonlocal audio_seconds
        turn_detection = None if OMNI_TURN_DETECTION in {"none", "off"} else {"type": OMNI_TURN_DETECTION}
        await ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": OMNI_VOICE,
                        "instructions": "你是一个实时多模态助手，请用中文简洁回答。",
                        "input_audio_format": "pcm",
                        "output_audio_format": "pcm",
                        "turn_detection": turn_detection,
                        "input_audio_transcription": {"model": "qwen3-asr-flash-realtime"},
                    },
                }
            )
        )
        for offset in range(0, len(pcm), chunk_bytes):
            chunk = pcm[offset : offset + chunk_bytes]
            await ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    }
                )
            )
            audio_seconds += len(chunk) / (SAMPLE_RATE * 2)
            await asyncio.sleep(CHUNK_MS / 1000)
        if image_b64:
            await ws.send(json.dumps({"type": "input_image_buffer.append", "image": image_b64}))
            logger.info("Image appended after %.2fs of audio", audio_seconds)
        if turn_detection is None:
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await ws.send(json.dumps({"type": "response.create"}))
        logger.info("Audio stream finished (%.2fs sent)", audio_seconds)

    async def receive_events() -> None:
        async for raw in ws:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[event] <non-JSON frame {len(raw)} bytes>")
                continue
            event_type = event.get("type", "?")
            if event_type == "response.audio.delta":
                print(f"[event] response.audio.delta ({len(event.get('delta') or '')} b64 chars)")
            else:
                print(f"[event] {json.dumps(event, ensure_ascii=False)[:500]}")
            if event_type == "response.done":
                response_done.set()
            if event_type == "error":
                response_done.set()

    async with ws:
        sender = asyncio.create_task(send_events())
        receiver = asyncio.create_task(receive_events())
        try:
            await asyncio.wait_for(response_done.wait(), timeout=seconds + 45)
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for response.done; closing")
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

    logger.info("Smoke test finished")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wav", help="Path to a pcm16 wav file to stream (default: generated sine)")
    parser.add_argument("--image", help="Path to a JPEG frame to append after the audio")
    parser.add_argument("--seconds", type=float, default=3.0, help="Seconds of generated audio (default: 3)")
    args = parser.parse_args()
    asyncio.run(run_smoke_test(args.wav, args.image, args.seconds))


if __name__ == "__main__":
    sys.exit(main())
