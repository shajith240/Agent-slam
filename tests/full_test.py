"""
Full 10-Minute Auto-Opponent Test — Agent SLAM 2026
=====================================================
Runs a test server + auto-injects realistic PRO arguments.
Bot argues CON. 600 second match.

Usage:
  Terminal 1:  python3 tests/full_test.py
  Terminal 2:  SANDBOX_WS_URL=ws://localhost:8766 python3 agent.py --sandbox \
                 --topic "The US tariffs imposed in 2025 will do more harm than good to the global economy" \
                 --pros team1 --cons team2
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
    format="%(asctime)s [TEST] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("full_test")

PORT = 8766
MATCH_DURATION = 600  # 10 minutes
BOT_TEAM = "team2"    # bot is CON
OPP_TEAM = "team1"    # auto-opponent is PRO
TOPIC = "The US tariffs imposed in 2025 will do more harm than good to the global economy"

# ── Pre-written PRO arguments (opponent) ─────────────────────────────────
# These are realistic competition-quality arguments arguing that tariffs DO more harm than good.
# Argument #3 includes a real URL to test Jina fetch.

OPPONENT_ARGUMENTS = [
    # 1. Opening (PRO)
    (
        "The US tariffs imposed in 2025 represent the most destructive trade policy in modern "
        "American history. First, these tariffs function as a hidden tax on American consumers. "
        "The Peterson Institute for International Economics estimates that the 2025 tariff regime "
        "will cost the average American household between $1,900 and $2,600 annually in higher "
        "prices across everyday goods from electronics to groceries. Second, the tariffs have "
        "triggered retaliatory measures from every major trading partner. The EU imposed 25% "
        "counter-tariffs on American agricultural exports, China restricted rare earth mineral "
        "exports critical to US tech manufacturing, and Canada levied duties on American energy "
        "products. This cascade of retaliation shrinks global trade volume and harms exporters "
        "on all sides. Third, global supply chains built over decades cannot be rewired overnight. "
        "Companies are not reshoring manufacturing to America — they are redirecting to Vietnam, "
        "India, and Mexico, meaning American workers see no job gains while paying higher prices. "
        "The tariffs are economic self-harm disguised as strength."
    ),

    # 2. Rebuttal
    (
        "The CON side claims tariffs protect American jobs, but this is empirically false. "
        "The Brookings Institution found that for every steel job saved by Trump-era tariffs, "
        "approximately 16 jobs were lost in steel-consuming industries like automotive and "
        "construction. The 2025 tariffs extend this damage to electronics, textiles, and "
        "agriculture. American soybean farmers lost $12 billion in export revenue after China's "
        "retaliatory tariffs, requiring massive taxpayer-funded bailouts that exceeded the "
        "tariff revenue collected. The CON side cannot escape this math: tariffs save a visible "
        "few while quietly destroying livelihoods for millions. Furthermore, the IMF downgraded "
        "its 2025 global growth forecast by 0.5 percentage points specifically citing US trade "
        "policy uncertainty. When the world's largest economy weaponizes trade, everyone suffers."
    ),

    # 3. Cross-examination WITH a real URL (tests Jina fetch)
    (
        "My opponent ignores the devastating impact on developing nations who depend on open "
        "trade with the US. According to the World Bank, US tariffs in 2025 threaten to push "
        "an additional 26 million people into extreme poverty across Southeast Asia and Sub-Saharan "
        "Africa. These nations built their economies around exporting to American markets — when "
        "those markets close, factories shut down and workers have nowhere to go. The Yale Budget "
        "Lab analysis shows tariffs effectively function as one of the largest tax increases in "
        "decades (Source: https://budgetlab.yale.edu/research/where-we-stand-fiscal-economic-and-distributional-effects-all-us-tariffs-enacted-2025-through-april). "
        "The CON side talks about American strength but cannot explain why US manufacturing "
        "output has actually declined in the three months since the broadest tariffs took effect. "
        "Protectionism does not protect — it isolates, and isolation in a globalized economy is "
        "economic suicide."
    ),

    # 4. Defense
    (
        "The CON side keeps citing selective job numbers while ignoring the macroeconomic "
        "devastation. US consumer confidence dropped to its lowest level since 2008 following "
        "the tariff announcements. The S&P 500 lost $4.2 trillion in market value in the two "
        "weeks after the April 2025 tariff escalation. Small businesses that import materials "
        "are being crushed — the National Federation of Independent Business reports that 67% "
        "of small manufacturers have seen input costs rise by over 15%. These are not Wall Street "
        "problems, these are Main Street disasters. My opponent argues that tariffs generate "
        "revenue, but the Congressional Budget Office projects that tariff revenue will be "
        "offset three times over by reduced economic activity, lower tax receipts, and increased "
        "government spending on bailouts for affected industries. The net fiscal impact is deeply "
        "negative. Every serious economist agrees: tariffs are a lose-lose proposition."
    ),

    # 5. Late defense / pressure
    (
        "Let me address the CON side's fundamental logical flaw: they confuse short-term "
        "disruption with long-term benefit. Yes, tariffs may temporarily boost output in a "
        "handful of protected industries. But economic history is unambiguous — protectionist "
        "policies have NEVER produced sustained prosperity. The Smoot-Hawley Tariff Act of 1930 "
        "deepened the Great Depression. Argentina's decades of import substitution left it an "
        "economic basket case. India's pre-1991 protectionism kept hundreds of millions in "
        "poverty until liberalization unleashed growth. The US tariffs of 2025 repeat these "
        "mistakes at unprecedented scale. Meanwhile, countries maintaining open trade — like "
        "Singapore, South Korea, and Germany — consistently outperform protectionist economies. "
        "The CON side has no historical precedent on their side. Not one protectionist economy "
        "has outperformed its free-trading peers over any sustained period."
    ),

    # 6. Closing (PRO)
    (
        "In conclusion, the evidence presented in this debate overwhelmingly confirms that US "
        "tariffs imposed in 2025 do more harm than good to the global economy. We have shown "
        "that tariffs cost American households thousands annually, triggered devastating retaliation "
        "from every major trading partner, pushed millions in developing nations toward poverty, "
        "crashed consumer confidence and stock markets, and crushed small businesses. The CON "
        "side offered no credible evidence that these harms are outweighed by benefits. They "
        "pointed to a handful of protected jobs while ignoring millions harmed. They claimed "
        "revenue gains while the CBO projects net fiscal losses. They invoked American strength "
        "while the data shows American decline. History teaches us that protectionism always "
        "fails. The 2025 tariffs are no exception. The judge should affirm the resolution: "
        "these tariffs do more harm than good, and the evidence is undeniable."
    ),
]


def ts():
    return datetime.now(timezone.utc).isoformat()


class TestMatch:
    def __init__(self):
        self.bot_ws = None
        self.started = False
        self.finish_time_ms = 0
        self.bot_msgs = 0
        self.opp_msgs = 0
        self.bot_connected = asyncio.Event()
        self.bot_responded = asyncio.Event()
        self.match_start_time = 0
        self.bot_response_times = []

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
                "team1": OPP_TEAM,
                "team2": BOT_TEAM,
                "topic": TOPIC,
                "description": "",
                "round": "Round 1",
                "finishTime": self.finish_time_ms,
                "pros": OPP_TEAM,
                "cons": BOT_TEAM,
                "turn": turn,
                "status": status,
                "remainingTime": int(self.remaining_seconds() * 1000),
            },
        }


match_state = TestMatch()


async def send(ws, payload):
    try:
        raw = json.dumps(payload)
        await ws.send(raw)
        msg_type = payload.get("type", "?")
        if msg_type == "debate-message":
            text = payload.get("data", {}).get("message", "")
            log.info("-> [%s] %d chars", msg_type, len(text))
        else:
            log.info("-> [%s] %s", msg_type, raw[:100])
    except Exception as e:
        log.warning("Send failed: %s", e)


async def handle_bot(ws, first_msg=None):
    match_state.bot_ws = ws
    match_state.bot_connected.set()
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
                match_state.bot_msgs += 1
                elapsed = time.time() - match_state.match_start_time if match_state.match_start_time else 0
                remaining = match_state.remaining_seconds()

                log.info("")
                log.info("=" * 70)
                log.info("BOT ARGUMENT #%d | %d chars | T+%.0fs | %ds remaining",
                         match_state.bot_msgs, len(text), elapsed, int(remaining))
                log.info("=" * 70)
                log.info(text[:200] + "..." if len(text) > 200 else text)
                log.info("=" * 70)

                # Validation checks
                issues = []
                if len(text) > 3000:
                    issues.append(f"OVER 3000 CHARS: {len(text)}")
                if "**" in text or "##" in text or "```" in text:
                    issues.append("CONTAINS MARKDOWN")
                if len(text) < 500:
                    issues.append(f"TOO SHORT: {len(text)} chars")

                if issues:
                    for issue in issues:
                        log.warning("ISSUE: %s", issue)
                else:
                    log.info("CHECKS PASSED: length OK, no markdown")

                await send(ws, {
                    "type": "info", "from": "system", "timestamp": ts(),
                    "data": {"message": "argument received"},
                })

                # Switch turn to opponent
                await send(ws, match_state.match_state("started", OPP_TEAM))

                # Signal that bot responded
                match_state.bot_responded.set()
            else:
                log.debug("Bot sent: %s", msg_type)

    except websockets.exceptions.ConnectionClosed:
        log.info("Bot disconnected")
    finally:
        match_state.bot_ws = None


async def handler(ws):
    log.info("New connection from %s", ws.remote_address)

    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        msg = json.loads(raw) if raw else {}
    except (asyncio.TimeoutError, json.JSONDecodeError):
        msg = {}

    await handle_bot(ws, first_msg=msg)


async def run_opponent():
    """Auto-opponent logic: waits for bot, starts match, injects arguments."""

    log.info("Waiting for bot to connect...")
    await match_state.bot_connected.wait()
    await asyncio.sleep(1)

    # ── Start match ──
    match_state.finish_time_ms = int(time.time() * 1000) + MATCH_DURATION * 1000
    match_state.started = True
    match_state.match_start_time = time.time()

    log.info("")
    log.info("#" * 70)
    log.info("  MATCH STARTING")
    log.info("  Topic: %s", TOPIC)
    log.info("  Bot: %s (CON) vs Opponent: %s (PRO)", BOT_TEAM, OPP_TEAM)
    log.info("  Duration: %ds", MATCH_DURATION)
    log.info("#" * 70)
    log.info("")

    await send(match_state.bot_ws, {
        "type": "welcome", "from": "system", "timestamp": ts(),
        "data": {"message": "Welcome to Agent SLAM Test Server"},
    })

    # Send match-state with bot's turn (triggers opening)
    await send(match_state.bot_ws, match_state.match_state("started", BOT_TEAM))

    # ── Wait for bot opening, then exchange arguments ──
    for i, opp_arg in enumerate(OPPONENT_ARGUMENTS):
        # Wait for bot to respond (with timeout)
        match_state.bot_responded.clear()
        try:
            await asyncio.wait_for(match_state.bot_responded.wait(), timeout=150)
        except asyncio.TimeoutError:
            log.error("BOT DID NOT RESPOND WITHIN 150s — POTENTIAL DQ SCENARIO")
            break

        remaining = match_state.remaining_seconds()

        if remaining < 30:
            log.info("Only %ds remaining — skipping further opponent messages", int(remaining))
            break

        if not match_state.bot_ws:
            log.error("Bot disconnected — aborting test")
            break

        # Wait a few seconds (simulate opponent thinking)
        wait = 5 if i == 0 else 8
        log.info("Opponent thinking for %ds... (%ds remaining)", wait, int(remaining))
        await asyncio.sleep(wait)

        # Inject opponent argument
        match_state.opp_msgs += 1
        elapsed = time.time() - match_state.match_start_time

        log.info("")
        log.info("-" * 70)
        log.info("OPPONENT #%d (PRO) | %d chars | T+%.0fs | %ds remaining",
                 match_state.opp_msgs, len(opp_arg), elapsed, int(match_state.remaining_seconds()))
        log.info("-" * 70)
        log.info(opp_arg[:200] + "..." if len(opp_arg) > 200 else opp_arg)
        log.info("-" * 70)

        await send(match_state.bot_ws, {
            "type": "debate-message", "from": OPP_TEAM,
            "timestamp": ts(), "data": {"message": opp_arg},
        })

        await asyncio.sleep(0.3)
        await send(match_state.bot_ws, match_state.match_state("started", BOT_TEAM))

    # ── Wait for final bot response ──
    match_state.bot_responded.clear()
    try:
        await asyncio.wait_for(match_state.bot_responded.wait(), timeout=150)
    except asyncio.TimeoutError:
        log.warning("Bot did not send final response within 150s")

    # ── End match ──
    await asyncio.sleep(2)
    elapsed = time.time() - match_state.match_start_time

    if match_state.bot_ws:
        await send(match_state.bot_ws, {
            "type": "match-finish", "from": "system", "timestamp": ts(),
            "data": {"message": "Match ended."},
        })

    log.info("")
    log.info("#" * 70)
    log.info("  MATCH FINISHED")
    log.info("  Duration: %.0fs", elapsed)
    log.info("  Bot messages: %d", match_state.bot_msgs)
    log.info("  Opponent messages: %d", match_state.opp_msgs)
    log.info("#" * 70)
    log.info("")
    log.info("Check bot terminal for full transcript and cost breakdown.")
    log.info("Also check: tail -f logs/transcript_*.txt")


async def main():
    log.info("=" * 70)
    log.info("  Agent SLAM — Full 10-Minute Auto-Opponent Test")
    log.info("  ws://localhost:%d", PORT)
    log.info("=" * 70)
    log.info("")
    log.info("Now run in another terminal:")
    log.info('  SANDBOX_WS_URL=ws://localhost:%d python3 agent.py --sandbox \\', PORT)
    log.info('    --topic "%s" \\', TOPIC)
    log.info('    --pros %s --cons %s', OPP_TEAM, BOT_TEAM)
    log.info("")
    log.info("=" * 70)

    server = await websockets.serve(handler, "localhost", PORT)

    # Run opponent logic concurrently
    await run_opponent()

    # Keep server alive a few more seconds for clean shutdown
    await asyncio.sleep(5)
    server.close()
    await server.wait_closed()
    log.info("Test complete. Server shut down.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Test interrupted.")
