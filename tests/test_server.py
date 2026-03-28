"""
Test Server — Agent SLAM 2026
==============================
WebSocket broker + interactive opponent via stdin.

Usage:
  Terminal 1:  python3 tests/test_server.py
  Terminal 2:  SANDBOX_WS_URL=ws://localhost:8766 python3 agent.py --sandbox \
                 --topic "..." --pros team1 --cons team2

Server starts the match automatically when bot connects.
Then paste opponent messages into Terminal 1 when prompted.
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone

import websockets
import websockets.exceptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SERVER] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_server")

PORT = 8766
MATCH_DURATION = 150
BOT_TEAM = "team2"    # must match TEAM_NAME in .env
OPP_TEAM = "team1"
TOPIC = "The US tariffs imposed in 2025 will do more harm than good to the global economy"


def ts():
    return datetime.now(timezone.utc).isoformat()


class Match:
    def __init__(self):
        self.bot_ws = None
        self.started = False
        self.finish_time_ms = 0
        self.bot_msgs = 0
        self.opp_msgs = 0
        self.bot_connected = asyncio.Event()
        self.bot_responded = asyncio.Event()
        self.match_start_time = 0

    def remaining_seconds(self):
        if self.finish_time_ms == 0:
            return MATCH_DURATION
        return max(0, (self.finish_time_ms - int(time.time() * 1000)) / 1000)

    def match_state(self, status, turn):
        return {
            "type": "match-state",
            "from": "system",
            "timestamp": ts(),
            "data": {
                "team1": "team1",
                "team2": "team2",
                "topic": TOPIC,
                "description": "",
                "round": "Round 1",
                "finishTime": self.finish_time_ms,
                "pros": BOT_TEAM,    # bot is PRO
                "cons": OPP_TEAM,    # opponent is CON
                "turn": turn,
                "status": status,
                "remainingTime": int(self.remaining_seconds() * 1000),
            },
        }


match = Match()


async def send(ws, payload):
    try:
        raw = json.dumps(payload)
        await ws.send(raw)
    except Exception as e:
        log.warning("Send failed: %s", e)


async def handle_bot(ws, first_msg=None):
    match.bot_ws = ws
    match.bot_connected.set()
    log.info("Bot connected")

    if first_msg and first_msg.get("type") == "auth":
        log.info("Bot auth received (ignored locally)")

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type in ("debate-message", "sandbox-message"):
                text = msg.get("data", {}).get("message", "")
                match.bot_msgs += 1
                elapsed = time.time() - match.match_start_time if match.match_start_time else 0
                remaining = match.remaining_seconds()

                print()
                print("=" * 70)
                print(f"BOT #{match.bot_msgs} | {len(text)} chars | T+{elapsed:.0f}s | {remaining:.0f}s left")
                print("=" * 70)
                print(text)
                print("=" * 70)

                # Validation
                issues = []
                if len(text) > 3000:
                    issues.append(f"OVER LIMIT: {len(text)} chars")
                if "**" in text or "##" in text or "```" in text:
                    issues.append("MARKDOWN DETECTED")
                if len(text) < 500:
                    issues.append(f"SHORT: {len(text)} chars")
                for issue in issues:
                    print(f"  WARNING: {issue}")
                if not issues:
                    print("  CHECKS: OK")

                await send(ws, {
                    "type": "info", "from": "system", "timestamp": ts(),
                    "data": {"message": "argument received"},
                })
                await send(ws, match.match_state("started", OPP_TEAM))

                # Signal for opponent input
                match.bot_responded.set()
            else:
                log.debug("Bot sent: %s", msg_type)

    except websockets.exceptions.ConnectionClosed:
        log.info("Bot disconnected")
    finally:
        match.bot_ws = None


async def handler(ws):
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        msg = json.loads(raw) if raw else {}
    except (asyncio.TimeoutError, json.JSONDecodeError):
        msg = {}
    await handle_bot(ws, first_msg=msg)


async def read_stdin_line():
    """Read a line from stdin without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sys.stdin.readline)


async def match_timer():
    """Auto-end the match when time expires."""
    while match.remaining_seconds() > 0:
        await asyncio.sleep(1)

    print(f"\n\nTIME'S UP!")
    if match.bot_ws:
        await send(match.bot_ws, {
            "type": "match-finish", "from": "system", "timestamp": ts(),
            "data": {"message": "Match ended."},
        })
    match.started = False
    elapsed = time.time() - match.match_start_time
    print()
    print("#" * 70)
    print(f"  MATCH FINISHED — timer expired at {elapsed:.0f}s")
    print(f"  Bot messages: {match.bot_msgs}")
    print(f"  Opponent messages: {match.opp_msgs}")
    print("#" * 70)


async def opponent_loop():
    """Wait for bot responses, then prompt for opponent input."""
    log.info("Waiting for bot to connect...")
    await match.bot_connected.wait()
    await asyncio.sleep(1)

    # Start match
    match.finish_time_ms = int(time.time() * 1000) + MATCH_DURATION * 1000
    match.started = True
    match.match_start_time = time.time()

    # Start auto-end timer in background
    asyncio.create_task(match_timer())

    print()
    print("#" * 70)
    print(f"  MATCH STARTED — {MATCH_DURATION}s")
    print(f"  Topic: {TOPIC}")
    print(f"  Bot: {BOT_TEAM} (PRO) | Opponent: {OPP_TEAM} (CON)")
    print("#" * 70)
    print()

    await send(match.bot_ws, {
        "type": "welcome", "from": "system", "timestamp": ts(),
        "data": {"message": "Welcome to Agent SLAM Test Server"},
    })
    await send(match.bot_ws, match.match_state("started", BOT_TEAM))

    while match.started:
        # Wait for bot to respond
        match.bot_responded.clear()
        try:
            await asyncio.wait_for(match.bot_responded.wait(), timeout=180)
        except asyncio.TimeoutError:
            if not match.started:
                break
            print("\nBOT DID NOT RESPOND IN 180s — DQ SCENARIO")
            break

        if not match.started or not match.bot_ws:
            break

        remaining = match.remaining_seconds()
        if remaining <= 0:
            break

        # Prompt for opponent message
        print()
        print(f"[{remaining:.0f}s remaining] Paste opponent PRO argument:")
        print("(Paste text, then press Enter on an empty line to send)")

        lines = []
        while match.started:
            try:
                line = await asyncio.wait_for(read_stdin_line(), timeout=5)
                line = line.rstrip('\n')
                if line == '' and lines:
                    break
                lines.append(line)
            except asyncio.TimeoutError:
                # Check if match ended while waiting for input
                if not match.started:
                    return
                continue

        if not match.started:
            break

        text = '\n'.join(lines).strip()
        if not text:
            continue

        match.opp_msgs += 1
        print(f"\nInjecting opponent #{match.opp_msgs}: {len(text)} chars")

        await send(match.bot_ws, {
            "type": "debate-message", "from": OPP_TEAM,
            "timestamp": ts(), "data": {"message": text},
        })
        await asyncio.sleep(0.3)
        await send(match.bot_ws, match.match_state("started", BOT_TEAM))


async def main():
    print("=" * 70)
    print("  Agent SLAM — Interactive Test Server")
    print(f"  ws://localhost:{PORT}")
    print("=" * 70)
    print()
    print("Run in another terminal:")
    print(f'  SANDBOX_WS_URL=ws://localhost:{PORT} python3 agent.py --sandbox \\')
    print(f'    --topic "{TOPIC}" \\')
    print(f'    --pros {OPP_TEAM} --cons {BOT_TEAM}')
    print()
    print("=" * 70)

    server = await websockets.serve(handler, "localhost", PORT)
    await opponent_loop()
    # Wait for timer to fully expire + cleanup
    while match.started:
        await asyncio.sleep(1)
    await asyncio.sleep(5)
    server.close()
    await server.wait_closed()
    print("\nServer shut down.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted.")
