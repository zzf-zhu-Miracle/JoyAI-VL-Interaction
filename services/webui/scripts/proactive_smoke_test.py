#!/usr/bin/env python3
"""Standalone sanity check for the ProactiveWatcher (主动播报).

Instantiates ProactiveWatcher with a fake inject callback (prints the alert),
submits a JPEG (CLI arg) a few times, and runs the watch loop for a while,
printing stats and whether the silence/alert path fired.

Calls the real Bailian chat completions API; requires DASHSCOPE_API_KEY.

Usage:
    python proactive_smoke_test.py --image frame.jpg [--seconds 10] [--interval 3]

Requires: pip install httpx
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from joy_interaction_webui.proactive_watcher import ProactiveWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("proactive_smoke_test")


async def run_smoke_test(image_path: str, seconds: float, interval: float) -> None:
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        raise SystemExit("DASHSCOPE_API_KEY is not set; export it before running this script")

    with open(image_path, "rb") as image_file:
        jpeg_bytes = image_file.read()
    logger.info("Loaded image: %s (%s bytes)", image_path, len(jpeg_bytes))

    injected = []

    async def fake_inject(text: str) -> None:
        injected.append(text)
        print(f"[INJECT] would speak alert: {text}")

    watcher = ProactiveWatcher(
        "smoke",
        inject_callback=fake_inject,
        interval_s=interval,
        cooldown_s=0.0,  # disable cooldown so repeated submissions can re-alert
    )
    watcher.start()

    deadline = asyncio.get_event_loop().time() + seconds
    submits = 0
    while asyncio.get_event_loop().time() < deadline:
        watcher.submit_frame(jpeg_bytes)
        submits += 1
        await asyncio.sleep(min(1.0, interval))

    await watcher.stop()

    stats = watcher.stats()
    print(f"[RESULT] submits={submits} stats={stats} injected_alerts={len(injected)}")
    if stats["checks"] == 0:
        print("[RESULT] WARNING: no checks ran (interval too long for the run duration?)")
    elif injected:
        print("[RESULT] alert path fired")
    else:
        print("[RESULT] silence path only (model output stayed </silence>)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--image", required=True, help="Path to a JPEG frame to submit")
    parser.add_argument("--seconds", type=float, default=10.0, help="Run duration (default: 10)")
    parser.add_argument("--interval", type=float, default=3.0, help="Check interval in seconds (default: 3)")
    args = parser.parse_args()
    asyncio.run(run_smoke_test(args.image, args.seconds, args.interval))


if __name__ == "__main__":
    sys.exit(main())
