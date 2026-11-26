#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pexpect", "pyte"]
# ///
import os
import random
import sys
import time

import pexpect
import pyte

PROMPT = os.environ.get("DEMO_PROMPT") or (
    "Importing the csv crashes on the N/A row. Skip bad rows with a warning, "
    "and add a top_categories(expenses, n=3) helper to report.py"
)
IDLE = float(os.environ.get("DEMO_IDLE", "6"))
LIMIT = float(os.environ.get("DEMO_LIMIT", "300"))
out = sys.stdout.buffer
screen = pyte.Screen(int(os.environ.get("DEMO_COLS", "100")), int(os.environ.get("DEMO_ROWS", "34")))
stream = pyte.ByteStream(screen)


def shown(text: str) -> bool:
    return text in " ".join(" ".join(screen.display).split())


def pump(timeout: float) -> bool:
    try:
        data = child.read_nonblocking(65536, timeout)
    except pexpect.TIMEOUT:
        return False
    except pexpect.EOF:
        raise SystemExit(0) from None
    out.write(data)
    out.flush()
    stream.feed(data)
    return True


def wait_idle(quiet: float, limit: float) -> None:
    end = time.monotonic() + limit
    last = time.monotonic()
    while time.monotonic() < end and time.monotonic() - last < quiet:
        if pump(0.2):
            last = time.monotonic()
        if shown("Do you want to proceed"):
            child.send("\r")
            time.sleep(1)


env = {**{k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC_"))}, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
child = pexpect.spawn(
    "claude",
    ["--model", "sonnet", "--permission-mode", "acceptEdits", *sys.argv[1:]],
    dimensions=(int(os.environ.get("DEMO_ROWS", "34")), int(os.environ.get("DEMO_COLS", "100"))),
    env=env,
)
wait_idle(3, 40)
if shown("trust this folder"):
    child.send("\x1b[B")
    time.sleep(0.3)
    child.send("\r")
    wait_idle(3, 40)
if os.environ.get("DEMO_TRUST"):
    child.send("\x03")
    child.send("\x03")
    child.close()
    raise SystemExit(0)
for ch in PROMPT:
    child.send(ch)
    time.sleep(random.uniform(0.02, 0.06))
    pump(0.0)
time.sleep(0.6)
child.send("\r")
wait_idle(IDLE, LIMIT)
time.sleep(1.5)
child.send("\x03")
child.send("\x03")
child.close()
