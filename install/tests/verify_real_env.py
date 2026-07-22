from __future__ import annotations

import importlib
import shutil
import sys


BASE_MODULES = [
    "aiohttp",
    "aiortc",
    "av",
    "cv2",
    "httpx",
    "joy_interaction_webui.server",
    "numpy",
    "openai",
    "PIL",
    "psutil",
    "websockets",
]

OPTIONAL_MODULES = {
    "no_with": [],
    "with_background_agent": ["fastapi", "uvicorn", "pydantic", "codex_api.main"],
    "with_all": ["fastapi", "uvicorn", "pydantic", "codex_api.main"],
}

OPTIONAL_COMMANDS = {
    "no_with": [],
    "with_background_agent": ["streamingharness-codex-api"],
    "with_all": ["streamingharness-codex-api"],
}


def import_module(name: str) -> None:
    importlib.import_module(name)
    print(f"import ok: {name}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_real_env.py <case-name>", file=sys.stderr)
        return 2

    case = sys.argv[1]
    if case not in OPTIONAL_MODULES:
        print(f"unknown case: {case}", file=sys.stderr)
        return 2

    for module in BASE_MODULES + OPTIONAL_MODULES[case]:
        import_module(module)

    for command in ["joy-interaction-webui", "joy-interaction-webui-stop", *OPTIONAL_COMMANDS[case]]:
        resolved = shutil.which(command)
        print(f"command {command}: {resolved}")
        if not resolved:
            raise RuntimeError(f"missing command: {command}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
