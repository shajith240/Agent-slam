import asyncio
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

os.makedirs("logs", exist_ok=True)

timestamp = time.strftime("%Y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/agent_{timestamp}.log"),
    ],
)

logger = logging.getLogger(__name__)

from src.config import WS_URL, SANDBOX_WS_URL, TEAM_NAME, ANTHROPIC_API_KEY, MODEL
from src.state_machine import MatchState
from src.debate_engine import DebateEngine
from src.ws_client import WSClient


def check_environment() -> bool:
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not set in .env")
        return False
    logger.info("API key loaded")

    if not TEAM_NAME:
        logger.error("TEAM_NAME is not set in .env")
        return False
    logger.info("Team name: %s", TEAM_NAME)

    return True


async def main() -> None:
    if not check_environment():
        sys.exit(1)

    sandbox = "--sandbox" in sys.argv

    if sandbox:
        ws_url = SANDBOX_WS_URL
        mode = "SANDBOX"
    else:
        ws_url = WS_URL
        mode = "LIVE"

    if not ws_url:
        logger.error(
            "WS_URL not set in .env — contact admin for match WebSocket URL"
        )
        sys.exit(1)

    logger.info("================================")
    logger.info("  AGENT SLAM 2026 — AGENT READY")
    logger.info("  Team: %s", TEAM_NAME)
    logger.info("  Mode: %s", mode)
    logger.info("  Model: %s + web_search", MODEL)
    logger.info("================================")

    state = MatchState()
    state.our_team = TEAM_NAME
    engine = DebateEngine()

    # Pass sandbox=True so WSClient sends "sandbox-message" type instead of
    # "debate-message" — the sandbox endpoint requires this different type.
    # Reference: User Manual section 6.4 (sandbox-message format).
    client = WSClient(ws_url, state, engine, sandbox=sandbox)

    try:
        await client.connect()
    except KeyboardInterrupt:
        logger.info("Interrupted by user — shutting down")
        client.stop()
        raise Exception(f"Interrupted by user handshake revoked")
    except Exception as e:
        logger.critical("Unhandled exception: %s", e)
        client.stop()
        raise Exception(f"During handling of previous error this error has occured {e}")
    finally:
        logger.info(engine.usage_summary())
        logger.info("Agent shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
