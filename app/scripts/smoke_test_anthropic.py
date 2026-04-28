"""
Phase D Commit 0 — Anthropic SDK smoke test.

Verifies that the SDK is installed, the API key is loaded, and a
minimal API call succeeds. Run this once after `pip install anthropic`
to confirm the .venv + .env are wired up before D1 starts using the
SDK in earnest.

Phase C constraint "no Claude SDK calls" is lifted for Phase D. This
script is the formal proof that the lift is complete.

Usage:
    C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.smoke_test_anthropic

Exit codes:
    0 — call succeeded; SDK + key + network all working
    1 — call failed (see stderr for the error class)
    2 — SDK not installed or key not loaded
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Load shared .env (override=True because the system env may carry an
# empty ANTHROPIC_API_KEY that would otherwise win the lookup).
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=False)


def main() -> int:
    try:
        import anthropic
    except ImportError:
        print("FAIL: anthropic SDK not installed", file=sys.stderr)
        print("Fix: C:/Users/olive/ValenceV3/.venv/Scripts/pip.exe install anthropic",
              file=sys.stderr)
        return 2

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("FAIL: ANTHROPIC_API_KEY not set in environment", file=sys.stderr)
        print("Fix: add ANTHROPIC_API_KEY=... to C:/Users/olive/ValenceV3/.env",
              file=sys.stderr)
        return 2

    print(f"anthropic SDK     : {anthropic.__version__}")
    print(f"ANTHROPIC_API_KEY : present ({key[:8]}..., {len(key)} chars)")

    # Minimal API call — 1 input token, 1 output token. Costs <$0.0001.
    # Used to prove network + auth work, not to test model capability.
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
    except anthropic.AuthenticationError as exc:
        print(f"FAIL: API key rejected — {exc}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError as exc:
        print(f"FAIL: network error — {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: unexpected error — {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    answer = response.content[0].text.strip()
    usage = response.usage
    print(f"model             : {response.model}")
    print(f"response          : {answer!r}")
    print(f"input_tokens      : {usage.input_tokens}")
    print(f"output_tokens     : {usage.output_tokens}")
    print()
    print("OK — Anthropic SDK + key + network all working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
