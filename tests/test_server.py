"""
Test Server — Agent SLAM 2026
==============================
Clean WebSocket broker for local testing.

Usage:
  python3 tests/test_server.py

Then:
  SANDBOX_WS_URL=ws://localhost:8766 python3 agent.py --sandbox --topic "..." --pros team1 --cons team2

Then open monitor/dashboard.html in a browser.
"""

import asyncio
import json
import logging
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


def ts():
    return datetime.now(timezone.utc).isoformat()


class Match:
    def __init__(self):
        self.bot_ws = None
        self.dash_ws = None
        self.dash_id = 0  # track which dashboard is current
        self.started = False
        self.topic = ""
        self.description = ""
        self.round = "Round 1"
        self.bot_team = "team1"
        self.duration = 900
        self.finish_time_ms = 0
        self.bot_msgs = 0
        self.opp_msgs = 0

    @property
    def opp_team(self):
        return "team2" if self.bot_team == "team1" else "team1"

    def remaining_ms(self):
        if self.finish_time_ms == 0:
            return self.duration * 1000
        return max(0, self.finish_time_ms - int(time.time() * 1000))

    def match_state(self, status, turn):
        return {
            "type": "match-state",
            "from": "system",
            "timestamp": ts(),
            "data": {
                "team1": self.bot_team if self.bot_team == "team1" else self.opp_team,
                "team2": self.opp_team if self.bot_team == "team1" else self.bot_team,
                "topic": self.topic,
                "description": self.description,
                "round": self.round,
                "finishTime": self.finish_time_ms,
                "pros": "team1",
                "cons": "team2",
                "turn": turn,
                "status": status,
                "remainingTime": self.remaining_ms(),
            },
        }


match = Match()


async def send(ws, payload):
    try:
        raw = json.dumps(payload)
        await ws.send(raw)
        log.info("-> [%s] %s", payload.get("type", "?"), raw[:120])
    except Exception as e:
        log.warning("Send failed: %s", e)


async def to_dash(payload):
    """Send to current dashboard. Safe to call if no dashboard connected."""
    if match.dash_ws:
        try:
            await match.dash_ws.send(json.dumps(payload))
        except Exception:
            pass


# ── Bot handler ──────────────────────────────────────

async def handle_bot(ws, first_msg=None):
    match.bot_ws = ws
    log.info("Bot connected")
    await to_dash({"type": "server-event", "data": {"message": "Bot connected"}})

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
                log.info("Bot argument #%d: %d chars", match.bot_msgs, len(text))

                await send(ws, {
                    "type": "info", "from": "system", "timestamp": ts(),
                    "data": {"message": "argument received"},
                })

                await to_dash({
                    "type": "debate-message", "from": match.bot_team,
                    "timestamp": ts(), "data": {"message": text},
                })

                # Switch turn to opponent
                await send(ws, match.match_state("started", match.opp_team))

                # Tell dashboard turn switched to opponent (their turn to paste)
                await to_dash(match.match_state("started", match.opp_team))

                await to_dash({
                    "type": "server-event",
                    "data": {"message": f"Bot sent #{match.bot_msgs} ({len(text)} chars). Paste opponent response."},
                })
            else:
                log.debug("Bot sent: %s", msg_type)

    except websockets.exceptions.ConnectionClosed:
        log.info("Bot disconnected")
    finally:
        match.bot_ws = None
        await to_dash({"type": "server-event", "data": {"message": "Bot disconnected"}})


# ── Dashboard handler ────────────────────────────────

async def handle_dash(ws):
    # Assign a unique ID so only the current dashboard can clear dash_ws
    match.dash_id += 1
    my_id = match.dash_id
    match.dash_ws = ws
    log.info("Dashboard #%d connected", my_id)

    await send(ws, {
        "type": "server-event",
        "data": {
            "message": "Dashboard connected",
            "botConnected": match.bot_ws is not None,
            "matchStarted": match.started,
        },
    })

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            cmd = msg.get("type", "")
            data = msg.get("data", {})

            if cmd == "start-match":
                await cmd_start(data)
            elif cmd == "inject-opponent":
                await cmd_inject(data)
            elif cmd == "set-time":
                await cmd_set_time(data)
            elif cmd == "end-match":
                await cmd_end()
            elif cmd == "dashboard-hello":
                log.info("Dashboard #%d hello", my_id)

    except websockets.exceptions.ConnectionClosed:
        log.info("Dashboard #%d disconnected", my_id)
    finally:
        # Only clear dash_ws if WE are still the current dashboard
        if match.dash_id == my_id:
            match.dash_ws = None


async def cmd_start(data):
    if not match.bot_ws:
        await to_dash({"type": "server-event", "data": {"message": "ERROR: Bot not connected. Start agent.py first."}})
        return

    # Prevent re-starting if match already running
    if match.started:
        log.warning("Match already in progress. Ignoring duplicate start-match.")
        await to_dash({"type": "server-event", "data": {"message": "Match already in progress. Ignoring duplicate start."}})
        return

    match.topic = data.get("topic", "AI will do more harm than good")
    match.description = data.get("description", "")
    match.round = data.get("round", "Round 1")
    match.bot_team = data.get("botTeam", "team1")
    match.duration = int(data.get("durationSeconds", 900))
    match.finish_time_ms = int(time.time() * 1000) + match.duration * 1000
    match.started = True
    match.bot_msgs = 0
    match.opp_msgs = 0

    log.info("Match starting — topic: %s | bot=%s | %ds",
             match.topic[:50], match.bot_team, match.duration)

    bot = match.bot_ws

    await send(bot, {
        "type": "welcome", "from": "system", "timestamp": ts(),
        "data": {"message": "Welcome to Agent SLAM Test Server"},
    })

    # Single match-state(started) — the ONLY trigger for opening argument
    await send(bot, match.match_state("started", match.bot_team))

    # Also send match-state to dashboard so it has full state
    await to_dash(match.match_state("started", match.bot_team))

    await to_dash({
        "type": "server-event",
        "data": {
            "message": "Match started — waiting for bot opening argument...",
            "finishTime": match.finish_time_ms,
            "botTeam": match.bot_team,
            "opponentTeam": match.opp_team,
            "topic": match.topic,
        },
    })


async def cmd_inject(data):
    text = data.get("message", "").strip()
    if not text:
        await to_dash({"type": "server-event", "data": {"message": "ERROR: Empty message."}})
        return
    if not match.bot_ws:
        await to_dash({"type": "server-event", "data": {"message": "ERROR: Bot not connected."}})
        return

    match.opp_msgs += 1
    log.info("Injecting opponent #%d: %d chars", match.opp_msgs, len(text))

    # Send opponent debate-message to bot
    await send(match.bot_ws, {
        "type": "debate-message", "from": match.opp_team,
        "timestamp": ts(), "data": {"message": text},
    })

    # Echo to dashboard
    await to_dash({
        "type": "debate-message", "from": match.opp_team,
        "timestamp": ts(), "data": {"message": text},
    })

    # Give bot its turn via match-state (single signal)
    await asyncio.sleep(0.3)
    await send(match.bot_ws, match.match_state("started", match.bot_team))

    await to_dash({
        "type": "server-event",
        "data": {"message": f"Opponent #{match.opp_msgs} sent — bot generating response..."},
    })


async def cmd_set_time(data):
    seconds = int(data.get("seconds", 120))
    match.finish_time_ms = int(time.time() * 1000) + seconds * 1000
    log.info("Timer set to %ds", seconds)

    if match.bot_ws:
        await send(match.bot_ws, match.match_state("started", match.opp_team))

    await to_dash({
        "type": "server-event",
        "data": {"message": f"Timer set to {seconds}s remaining.", "finishTime": match.finish_time_ms},
    })


async def cmd_end():
    log.info("Match ending")
    if match.bot_ws:
        await send(match.bot_ws, {
            "type": "match-finish", "from": "system", "timestamp": ts(),
            "data": {"message": "Match ended."},
        })
    match.started = False
    await to_dash({"type": "server-event", "data": {"message": "Match ended."}})


# ── Connection dispatcher ────────────────────────────

async def handler(ws):
    log.info("New connection from %s", ws.remote_address)

    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        msg = json.loads(raw) if raw else {}
    except (asyncio.TimeoutError, json.JSONDecodeError):
        msg = {}

    if msg.get("type") == "dashboard-hello":
        await handle_dash(ws)
    else:
        await handle_bot(ws, first_msg=msg)


async def main():
    log.info("=" * 50)
    log.info("  Agent SLAM — Test Server")
    log.info("  ws://localhost:%d", PORT)
    log.info("=" * 50)
    log.info("1. Open monitor/dashboard.html in browser")
    log.info("2. Run: SANDBOX_WS_URL=ws://localhost:%d python3 agent.py --sandbox", PORT)
    log.info("3. Configure match in dashboard -> Start")
    log.info("=" * 50)

    async with websockets.serve(handler, "localhost", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
