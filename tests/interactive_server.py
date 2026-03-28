"""
Interactive Test Server — Agent SLAM 2026
==========================================
Runs on ws://localhost:8766

Brokers two WebSocket connections:
  1. The bot  (agent.py with WS_URL=ws://localhost:8766)
  2. The dashboard  (tests/interactive_dashboard.html)

The dashboard injects opponent messages; the server forwards them to the bot
using the exact same JSON format as the real competition server.

Usage:
  python tests/interactive_server.py

Then in a second terminal:
  python agent.py          (with WS_URL=ws://localhost:8766 in .env)

Then open tests/interactive_dashboard.html in a browser.
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
log = logging.getLogger("interactive_server")

PORT = 8766

# ---------------------------------------------------------------------------
# Shared match state (single match, single bot)
# ---------------------------------------------------------------------------

class MatchSession:
    def __init__(self):
        self.bot_ws = None
        self.dashboard_ws = None

        # Match config (set by dashboard before start)
        self.topic = "Artificial Intelligence will do more harm than good to society"
        self.description = (
            "Debate whether AI's net impact on humanity is negative, considering "
            "job displacement, misinformation, surveillance, and existential risk "
            "versus economic growth, medical advances, and productivity gains."
        )
        self.round = "Round 1"
        self.bot_team = "team1"      # team1=PRO, team2=CON
        self.duration_seconds = 900  # match length

        # Runtime state
        self.started = False
        self.finish_time_ms = 0
        self.bot_message_count = 0
        self.opponent_message_count = 0

    @property
    def opponent_team(self):
        return "team2" if self.bot_team == "team1" else "team1"

    def get_finish_time_ms(self):
        return self.finish_time_ms

    def remaining_ms(self):
        if self.finish_time_ms == 0:
            return self.duration_seconds * 1000
        return max(0, self.finish_time_ms - int(time.time() * 1000))

    def build_match_state(self, status: str, turn: str) -> dict:
        team1_name = "OUR BOT" if self.bot_team == "team1" else "OPPONENT"
        team2_name = "OUR BOT" if self.bot_team == "team2" else "OPPONENT"
        return {
            "type": "match-state",
            "from": "system",
            "timestamp": ts(),
            "data": {
                "team1": team1_name,
                "team2": team2_name,
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


session = MatchSession()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now(timezone.utc).isoformat()


async def send(ws, payload: dict):
    try:
        await ws.send(json.dumps(payload))
        log.info("→ [%s] %s", payload.get("type", "?"), json.dumps(payload)[:100])
    except Exception as e:
        log.warning("Send failed: %s", e)


async def broadcast_to_dashboard(payload: dict):
    if session.dashboard_ws:
        try:
            await session.dashboard_ws.send(json.dumps(payload))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bot connection handler
# ---------------------------------------------------------------------------

async def handle_bot(ws):
    session.bot_ws = ws
    log.info("Bot connected")

    await broadcast_to_dashboard({
        "type": "server-event",
        "data": {"message": "Bot connected — waiting for match config from dashboard"},
    })

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON from bot: %s", raw[:80])
                continue

            msg_type = msg.get("type", "")
            log.info("← Bot [%s]", msg_type)

            if msg_type == "auth":
                log.info("Bot auth received (ignored for local test)")
                # Don't start match here — wait for dashboard to send start-match

            elif msg_type == "debate-message":
                text = msg.get("data", {}).get("message", "")
                session.bot_message_count += 1
                log.info("Bot argument #%d: %d chars", session.bot_message_count, len(text))

                # Acknowledge to bot
                await send(ws, {
                    "type": "info",
                    "from": "system",
                    "timestamp": ts(),
                    "data": {"message": "argument received"},
                })

                # Forward full message to dashboard
                await broadcast_to_dashboard({
                    "type": "debate-message",
                    "from": session.bot_team,
                    "timestamp": ts(),
                    "data": {"message": text},
                    "meta": {
                        "charCount": len(text),
                        "botMessageCount": session.bot_message_count,
                    },
                })

                # Set turn to opponent — bot waits
                await send(ws, session.build_match_state("started", session.opponent_team))

                # Notify dashboard it's their turn to inject
                await broadcast_to_dashboard({
                    "type": "server-event",
                    "data": {
                        "message": f"Bot sent argument #{session.bot_message_count}. "
                                   f"Paste opponent response and click Send.",
                        "waitingForOpponent": True,
                    },
                })

            elif msg_type == "sandbox-message":
                # Bot sent in sandbox mode — treat exactly like debate-message
                text = msg.get("data", {}).get("message", "")
                session.bot_message_count += 1
                log.info("Bot sandbox argument #%d: %d chars", session.bot_message_count, len(text))

                await send(ws, {
                    "type": "info",
                    "from": "system",
                    "timestamp": ts(),
                    "data": {"message": "argument received"},
                })

                await broadcast_to_dashboard({
                    "type": "debate-message",
                    "from": session.bot_team,
                    "timestamp": ts(),
                    "data": {"message": text},
                    "meta": {
                        "charCount": len(text),
                        "botMessageCount": session.bot_message_count,
                    },
                })

                await send(ws, session.build_match_state("started", session.opponent_team))

                await broadcast_to_dashboard({
                    "type": "server-event",
                    "data": {
                        "message": f"Bot sent argument #{session.bot_message_count}. "
                                   f"Paste opponent response and click Send.",
                        "waitingForOpponent": True,
                    },
                })

            else:
                log.debug("Bot sent unknown type: %s", msg_type)

    except websockets.exceptions.ConnectionClosed:
        log.info("Bot disconnected")
    finally:
        session.bot_ws = None
        await broadcast_to_dashboard({
            "type": "server-event",
            "data": {"message": "Bot disconnected"},
        })


# ---------------------------------------------------------------------------
# Dashboard connection handler
# ---------------------------------------------------------------------------

async def handle_dashboard(ws):
    session.dashboard_ws = ws
    log.info("Dashboard connected")

    # Send current state to newly connected dashboard
    await send(ws, {
        "type": "server-event",
        "data": {
            "message": "Dashboard connected",
            "botConnected": session.bot_ws is not None,
            "matchStarted": session.started,
        },
    })

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            data = msg.get("data", {})
            log.info("← Dashboard [%s]", msg_type)

            if msg_type == "start-match":
                await handle_start_match(data)

            elif msg_type == "inject-opponent":
                await handle_inject_opponent(data)

            elif msg_type == "set-time":
                await handle_set_time(data)

            elif msg_type == "end-match":
                await handle_end_match()

            elif msg_type == "dashboard-hello":
                log.info("Dashboard hello received")

    except websockets.exceptions.ConnectionClosed:
        log.info("Dashboard disconnected")
    finally:
        session.dashboard_ws = None


# ---------------------------------------------------------------------------
# Dashboard command handlers
# ---------------------------------------------------------------------------

async def handle_start_match(data: dict):
    if not session.bot_ws:
        await broadcast_to_dashboard({
            "type": "server-event",
            "data": {"message": "ERROR: Bot is not connected. Start agent.py first."},
        })
        return

    # Apply config from dashboard
    session.topic = data.get("topic", session.topic)
    session.description = data.get("description", session.description)
    session.round = data.get("round", session.round)
    session.bot_team = data.get("botTeam", session.bot_team)
    session.duration_seconds = int(data.get("durationSeconds", session.duration_seconds))
    session.finish_time_ms = int(time.time() * 1000) + session.duration_seconds * 1000
    session.started = True
    session.bot_message_count = 0
    session.opponent_message_count = 0

    log.info("Starting match — topic: %s | bot=%s | duration=%ds",
             session.topic[:50], session.bot_team, session.duration_seconds)

    bot = session.bot_ws

    # Step 1: Welcome
    await send(bot, {
        "type": "welcome",
        "from": "system",
        "timestamp": ts(),
        "data": {"message": "Welcome to Agent SLAM Interactive Test Server!"},
    })
    await asyncio.sleep(1)

    # Step 2: match-state active
    await send(bot, session.build_match_state("active", session.bot_team))
    await asyncio.sleep(1)

    # Step 3: match-update (starts the clock)
    await send(bot, {
        "type": "match-update",
        "from": "system",
        "timestamp": ts(),
        "data": {
            "message": f"The match has started! It's {session.bot_team}'s turn.",
            "finishTime": session.finish_time_ms,
        },
    })

    # Step 4: match-state started — triggers bot's opening argument
    await send(bot, session.build_match_state("started", session.bot_team))

    await broadcast_to_dashboard({
        "type": "server-event",
        "data": {
            "message": "Match started — waiting for bot opening argument...",
            "finishTime": session.finish_time_ms,
            "botTeam": session.bot_team,
            "opponentTeam": session.opponent_team,
            "topic": session.topic,
        },
    })


async def handle_inject_opponent(data: dict):
    message = data.get("message", "").strip()
    if not message:
        await broadcast_to_dashboard({
            "type": "server-event",
            "data": {"message": "ERROR: Empty opponent message — not sent."},
        })
        return

    if not session.bot_ws:
        await broadcast_to_dashboard({
            "type": "server-event",
            "data": {"message": "ERROR: Bot not connected."},
        })
        return

    session.opponent_message_count += 1
    log.info("Injecting opponent message #%d: %d chars",
             session.opponent_message_count, len(message))

    debate_msg = {
        "type": "debate-message",
        "from": session.opponent_team,
        "timestamp": ts(),
        "data": {"message": message},
    }

    # Send to bot
    await send(session.bot_ws, debate_msg)

    # Also echo to dashboard feed
    await broadcast_to_dashboard(debate_msg)

    # Give bot its turn (match-state with our team's turn)
    await asyncio.sleep(0.2)
    await send(session.bot_ws, session.build_match_state("started", session.bot_team))

    await broadcast_to_dashboard({
        "type": "server-event",
        "data": {
            "message": f"Opponent message #{session.opponent_message_count} sent — bot is generating response...",
            "waitingForBot": True,
        },
    })


async def handle_set_time(data: dict):
    seconds = int(data.get("seconds", 120))
    session.finish_time_ms = int(time.time() * 1000) + seconds * 1000
    log.info("Time set to %ds remaining", seconds)

    # Notify bot of new finish time via match-state
    if session.bot_ws:
        await send(session.bot_ws, session.build_match_state("started", session.opponent_team))

    await broadcast_to_dashboard({
        "type": "server-event",
        "data": {
            "message": f"Time set to {seconds}s remaining. "
                       f"{'Closing phase will trigger on next bot message (>=4 msgs sent).' if seconds < 180 else ''}",
            "finishTime": session.finish_time_ms,
        },
    })


async def handle_end_match():
    log.info("Ending match")
    if session.bot_ws:
        await send(session.bot_ws, {
            "type": "match-finish",
            "from": "system",
            "timestamp": ts(),
            "data": {"message": "Match ended by test controller."},
        })

    session.started = False
    await broadcast_to_dashboard({
        "type": "server-event",
        "data": {"message": "Match ended. Check bot terminal for usage summary."},
    })


# ---------------------------------------------------------------------------
# Main connection dispatcher
# ---------------------------------------------------------------------------

async def handler(ws):
    """Identify connection type by first message, then dispatch."""
    log.info("New connection from %s", ws.remote_address)

    # Wait up to 3s for first message to identify client type
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            msg = {}

        msg_type = msg.get("type", "")
        log.info("First message type: %s", msg_type)

        if msg_type == "dashboard-hello":
            await handle_dashboard(ws)
        else:
            # Treat as bot (auth message or timeout)
            # Re-process the auth message inside handle_bot by injecting it
            await handle_bot_with_first_message(ws, msg)

    except asyncio.TimeoutError:
        # No first message — assume it's the bot (no TEAM_EMAIL set)
        log.info("No first message — treating as bot connection")
        await handle_bot(ws)


async def handle_bot_with_first_message(ws, first_msg: dict):
    """Handle bot connection, processing the already-received first message."""
    session.bot_ws = ws
    log.info("Bot connected (first msg: %s)", first_msg.get("type", "?"))

    await broadcast_to_dashboard({
        "type": "server-event",
        "data": {"message": "Bot connected — configure match in dashboard and click Start Match"},
    })

    # Handle the first message
    if first_msg.get("type") == "auth":
        log.info("Bot auth received (ignored for local test)")

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "debate-message":
                text = msg.get("data", {}).get("message", "")
                session.bot_message_count += 1
                log.info("Bot argument #%d: %d chars", session.bot_message_count, len(text))

                await send(ws, {
                    "type": "info",
                    "from": "system",
                    "timestamp": ts(),
                    "data": {"message": "argument received"},
                })

                await broadcast_to_dashboard({
                    "type": "debate-message",
                    "from": session.bot_team,
                    "timestamp": ts(),
                    "data": {"message": text},
                    "meta": {
                        "charCount": len(text),
                        "botMessageCount": session.bot_message_count,
                    },
                })

                await send(ws, session.build_match_state("started", session.opponent_team))

                await broadcast_to_dashboard({
                    "type": "server-event",
                    "data": {
                        "message": f"Bot sent argument #{session.bot_message_count}. "
                                   f"Paste opponent response and click Send.",
                        "waitingForOpponent": True,
                    },
                })

            elif msg_type == "sandbox-message":
                # Bot sent in sandbox mode — treat exactly like debate-message
                text = msg.get("data", {}).get("message", "")
                session.bot_message_count += 1
                log.info("Bot sandbox argument #%d: %d chars", session.bot_message_count, len(text))

                await send(ws, {
                    "type": "info",
                    "from": "system",
                    "timestamp": ts(),
                    "data": {"message": "argument received"},
                })

                await broadcast_to_dashboard({
                    "type": "debate-message",
                    "from": session.bot_team,
                    "timestamp": ts(),
                    "data": {"message": text},
                    "meta": {
                        "charCount": len(text),
                        "botMessageCount": session.bot_message_count,
                    },
                })

                await send(ws, session.build_match_state("started", session.opponent_team))

                await broadcast_to_dashboard({
                    "type": "server-event",
                    "data": {
                        "message": f"Bot sent argument #{session.bot_message_count}. "
                                   f"Paste opponent response and click Send.",
                        "waitingForOpponent": True,
                    },
                })

    except websockets.exceptions.ConnectionClosed:
        log.info("Bot disconnected")
    finally:
        session.bot_ws = None
        await broadcast_to_dashboard({
            "type": "server-event",
            "data": {"message": "Bot disconnected"},
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    log.info("=" * 60)
    log.info("  AGENT SLAM — Interactive Test Server")
    log.info("  Listening on ws://localhost:%d", PORT)
    log.info("=" * 60)
    log.info("Step 1: Open tests/interactive_dashboard.html in browser")
    log.info("Step 2: Run: python agent.py  (with WS_URL=ws://localhost:%d)", PORT)
    log.info("Step 3: Configure match in dashboard → click Start Match")
    log.info("=" * 60)

    async with websockets.serve(handler, "localhost", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
