import asyncio
import json
import logging
import re
import time
import uuid

import websockets
import websockets.exceptions

from src.config import RECONNECT_WINDOW_SECONDS, MAX_RECONNECT_ATTEMPTS, TEAM_EMAIL, TEAM_PASSWORD
from src.state_machine import MatchState
from src.debate_engine import DebateEngine

logger = logging.getLogger(__name__)

# Separate debate transcript logger — clean readable log of just messages
_transcript = logging.getLogger("transcript")
_transcript.propagate = False  # don't duplicate into main log


class WSClient:

    def __init__(self, ws_url: str, state: MatchState, engine: DebateEngine, sandbox: bool = False):
        self.ws_url = ws_url
        self.state = state
        self.engine = engine
        self.ws = None
        self.connected = False
        self.running = True
        self.reconnect_attempts = 0
        self.last_disconnect_time = 0.0
        self.sandbox = sandbox
        self._turn_in_progress = False
        self._current_turn_id = ""
        self._last_sent_message = ""
        self._research_done = False

    async def connect(self) -> None:
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    self.connected = True
                    self.reconnect_attempts = 0
                    logger.info("Connected to match server")
                    await self.authenticate()
                    await self.listen()
            except websockets.exceptions.ConnectionClosed as e:
                self.connected = False
                self.last_disconnect_time = time.time()
                logger.error("Connection closed: %s", e)
                await self.handle_reconnect()
            except Exception as e:
                self.connected = False
                self.last_disconnect_time = time.time()
                logger.error("Connection error: %s", e)
                await self.handle_reconnect()

    async def authenticate(self) -> None:
        if not TEAM_EMAIL or not TEAM_PASSWORD:
            logger.info("No TEAM_EMAIL/TEAM_PASSWORD set — skipping auth handshake")
            return

        auth_payload = {
            "type": "auth",
            "data": {
                "email": TEAM_EMAIL,
                "password": TEAM_PASSWORD,
            },
        }
        await self.send_json(auth_payload)
        logger.info("Auth handshake sent for: %s", TEAM_EMAIL)

    async def handle_reconnect(self) -> None:
        self.reconnect_attempts += 1

        if self.reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
            logger.critical("Max reconnect attempts reached. Shutting down.")
            self.running = False
            return

        wait = min(2 ** self.reconnect_attempts, 30)

        if self.last_disconnect_time > 0:
            elapsed_since_disconnect = time.time() - self.last_disconnect_time
            if elapsed_since_disconnect > RECONNECT_WINDOW_SECONDS - 10:
                logger.critical(
                    "Reconnect window closing fast — attempting immediately"
                )
                wait = 0

        logger.info(
            "Reconnecting in %ds (attempt %d/%d)",
            wait, self.reconnect_attempts, MAX_RECONNECT_ATTEMPTS,
        )
        await asyncio.sleep(wait)

    async def listen(self) -> None:
        async for message in self.ws:
            try:
                parsed = json.loads(message)
                msg_type = parsed.get("type")
                data = parsed.get("data", {})

                if msg_type == "welcome":
                    logger.info("Welcome: %s", data.get("message", "connected"))

                elif msg_type == "match-state":
                    await self.handle_match_state(data)

                elif msg_type == "match-update":
                    await self.handle_match_update(data)

                elif msg_type == "debate-message":
                    await self.handle_debate_message(parsed)

                elif msg_type == "sandbox-message":
                    echo_msg = data.get("message", "")
                    logger.info("Sandbox echo received: %d chars", len(echo_msg))

                elif msg_type == "match-paused":
                    self.state.status = "paused"
                    self._turn_in_progress = False
                    logger.info("Match paused")

                elif msg_type == "match-resumed":
                    await self.handle_match_resumed(data)

                elif msg_type == "match-finish":
                    await self.handle_match_finish()

                elif msg_type == "previous-message":
                    await self.handle_previous_messages(data)

                elif msg_type == "user-joined":
                    logger.info("User joined: %s", data.get("message"))

                elif msg_type == "user-left":
                    logger.info("User left: %s", data.get("message"))

                elif msg_type == "error":
                    logger.warning("Server error: %s", data.get("message"))
                    _transcript.info("SERVER ERROR: %s", data.get("message"))

                elif msg_type == "info":
                    logger.info("Server info: %s", data.get("message"))

                else:
                    logger.debug("Unknown message type: %s", msg_type)

            except json.JSONDecodeError:
                logger.error("Failed to parse message: %s", message[:200])
            except Exception as e:
                logger.error("Error handling message: %s", e)

    async def handle_match_state(self, data: dict) -> None:
        self.state.update_from_match_state(data)
        logger.info(
            "State updated — turn: %s, status: %s, topic: %s",
            self.state.turn, self.state.status, self.state.topic,
        )
        if self.state.status == "started" and self.state.topic:
            _transcript.info(
                "MATCH STATE — topic: %s | stance: %s | turn: %s | remaining: %ds",
                self.state.topic, self.state.our_stance, self.state.turn,
                self.state.seconds_remaining_in_match,
            )

        # One-time research: fires when topic first arrives at match start
        if (self.state.topic
                and not self._research_done
                and self.state.status == "started"
                and self.engine.use_web_search):
            self._research_done = True
            logger.info("Topic received — running one-time research call (45s timeout)...")
            try:
                self.state.research_data = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.engine.research_topic,
                        self.state.topic,
                        self.state.our_stance,
                    ),
                    timeout=45,
                )
                logger.info("Research ready: %d chars", len(self.state.research_data))
            except asyncio.TimeoutError:
                logger.warning("Research timed out at 45s — proceeding without research data")
                self.state.research_data = ""

        if self.state.is_our_turn:
            logger.info("IT IS OUR TURN (via match-state) — generating argument")
            await self.take_turn()

    async def handle_match_update(self, data: dict) -> None:
        self.state.update_from_match_update(data)
        logger.info("Match started. Finish time set.")
        if self.state.is_our_turn:
            await self.take_turn()

    async def handle_debate_message(self, parsed: dict) -> None:
        team = parsed.get("from")
        data = parsed.get("data", {})
        message = data.get("message", "")
        timestamp = parsed.get("timestamp", "")

        if team != self.state.our_team:
            self.state.record_opponent_message(team, message, timestamp)
            logger.info("Opponent argued: %s...", message[:100])
            _transcript.info(
                "\n%s\n[OPPONENT] (%d chars):\n%s\n%s",
                "=" * 60, len(message), message, "=" * 60,
            )

            # If opponent cited a specific URL, fetch it via Jina so we can rebut directly
            if self.engine.use_web_search:
                urls = re.findall(r'https?://[^\s\)\"\'>]+', message)
                if urls:
                    url = urls[0].rstrip('.,;')
                    logger.info("Opponent cited URL — fetching via Jina: %s", url)
                    fetched = await asyncio.to_thread(self.engine.fetch_opponent_url, url)
                    if fetched:
                        self.state.research_data += (
                            f"\n\nOPPONENT CITED THIS SOURCE (read it and use it against them):\n"
                            f"URL: {url}\n{fetched}"
                        )
                        logger.info("Appended opponent source (%d chars) to research_data", len(fetched))

            if self.state.status == "started":
                self.state.turn = self.state.our_team
                self.state.turn_start_time = time.time()
                logger.info("IT IS OUR TURN (via debate-message) — generating argument")
                await self.take_turn()

    async def handle_match_resumed(self, data: dict) -> None:
        finish_time = data.get("finishTime")
        if finish_time:
            self.state.finish_time = finish_time
        self.state.status = "started"
        logger.info("Match resumed")
        if self.state.is_our_turn:
            await self.take_turn()

    async def handle_match_finish(self) -> None:
        self.running = False
        logger.info("Match finished. %s", self.engine.usage_summary())
        _transcript.info("\nMATCH FINISHED — %s", self.engine.usage_summary())

    async def handle_previous_messages(self, data: dict) -> None:
        conversations = data.get("conversations", [])
        self.state.load_previous_messages(conversations)
        logger.info("Loaded %d previous messages", len(conversations))

    async def take_turn(self) -> None:
        if not self.state.is_our_turn:
            logger.debug("take_turn called but not our turn, skipping")
            return

        if self.state.status != "started":
            logger.debug("take_turn called but match status is %s, skipping", self.state.status)
            return

        # Prevent sending twice without opponent responding (except opening)
        if self.state.message_count > 0 and self.state.conversation:
            if self.state.conversation[-1]["is_ours"]:
                logger.debug("Last message is ours — waiting for opponent, skipping")
                return

        turn_id = str(uuid.uuid4())[:8]

        if self._turn_in_progress:
            logger.debug("Turn already in progress (%s) — skipping duplicate trigger", self._current_turn_id)
            return
        self._turn_in_progress = True
        self._current_turn_id = turn_id

        if self.state.seconds_on_our_turn > 85:
            logger.warning(
                "Turn time exceeded 85s (%.1fs), skipping to avoid late send",
                self.state.seconds_on_our_turn,
            )
            self._turn_in_progress = False
            return

        call_mode = self.state.call_mode
        logger.info(
            "[turn:%s] call_mode=%s remaining=%ds avg_resp=%.1fs phase=%s",
            turn_id, call_mode,
            self.state.seconds_remaining_in_match,
            self.state.avg_response_time,
            self.state.debate_phase,
        )

        turn_start = time.time()

        try:
            # Route to correct generation method based on call_mode
            if call_mode == "critical":
                # Under 60s — try real emergency argument with tight timeout
                # Fall back to hardcoded ONLY if API fails
                try:
                    argument = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.engine.generate_emergency_argument, self.state
                        ),
                        timeout=30,
                    )
                    logger.warning("[turn:%s] CRITICAL mode — real argument generated", turn_id)
                except Exception as e:
                    from src.debate_engine import EMERGENCY_CLOSING_PRO, EMERGENCY_CLOSING_CON
                    argument = (
                        EMERGENCY_CLOSING_PRO
                        if self.state.our_stance == "PRO"
                        else EMERGENCY_CLOSING_CON
                    )
                    logger.critical("[turn:%s] CRITICAL mode — API failed (%s), using hardcoded fallback", turn_id, e)

            elif call_mode == "emergency":
                argument = await asyncio.to_thread(
                    self.engine.generate_emergency_argument, self.state
                )
                logger.warning("[turn:%s] EMERGENCY mode used", turn_id)

            elif call_mode == "caution":
                argument = await asyncio.to_thread(
                    self.engine.generate_caution_argument, self.state
                )
                logger.warning("[turn:%s] CAUTION mode used", turn_id)

            else:  # normal or fast
                argument = await asyncio.to_thread(
                    self.engine.generate_argument, self.state
                )
                logger.info("[turn:%s] NORMAL/FAST mode used", turn_id)

            # Record response time BEFORE duplicate check so timing is accurate
            elapsed = time.time() - turn_start
            # Skip recording opening time — research + generation inflates avg,
            # which poisons call_mode into emergency for the entire match
            if self.state.message_count > 0:
                self.state.record_response_time(elapsed)
                logger.info("[turn:%s] response_time=%.1fs (avg now %.1fs)", turn_id, elapsed, self.state.avg_response_time)
            else:
                logger.info("[turn:%s] opening response_time=%.1fs (NOT recorded in avg)", turn_id, elapsed)

            if argument == self._last_sent_message:
                logger.warning("Duplicate message detected, regenerating with caution mode")
                argument = await asyncio.to_thread(
                    self.engine.generate_caution_argument, self.state
                )

            if self.state.status != "started":
                logger.warning("Match state changed during generation (now %s), aborting send", self.state.status)
                return

            msg_type = "sandbox-message" if self.sandbox else "debate-message"

            payload = {
                "type": msg_type,
                "data": {"message": argument},
            }
            await self.send_json(payload)

            self._last_sent_message = argument
            # Mark closing as sent so we don't send a second closing
            if self.state.debate_phase == "closing":
                self.state.closing_sent = True
            self.state.record_our_message(argument)
            logger.info("Sent argument [%s] turn=%s: %d chars", msg_type, turn_id, len(argument))
            _transcript.info(
                "\n%s\n[US — %s] (%d chars, %.1fs, phase=%s):\n%s\n%s",
                "-" * 60, call_mode.upper(), len(argument), elapsed,
                self.state.debate_phase, argument, "-" * 60,
            )

        except Exception as e:
            logger.error("Failed to send argument: %s", e)
        finally:
            self._turn_in_progress = False

    async def send_json(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload))

    def stop(self) -> None:
        self.running = False
        self.connected = False
