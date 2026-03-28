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

# Debate transcript — clean readable file with just the debate messages
# Monitor live with:  tail -f logs/transcript_*.txt
_transcript_logger = logging.getLogger("transcript")
_transcript_logger.setLevel(logging.INFO)
_th = logging.FileHandler(f"logs/transcript_{timestamp}.txt")
_th.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
_transcript_logger.addHandler(_th)

logger = logging.getLogger(__name__)

from src.config import WS_URL, SANDBOX_WS_URL, TEAM_NAME, ANTHROPIC_API_KEY, MODEL
from src.state_machine import MatchState
from src.debate_engine import DebateEngine
from src.ws_client import WSClient


def check_environment() -> bool:
    ok = True

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not set in .env")
        ok = False
    else:
        logger.info("API key loaded (%s...%s)", ANTHROPIC_API_KEY[:4], ANTHROPIC_API_KEY[-4:])

    if not TEAM_NAME:
        logger.error("TEAM_NAME is not set in .env")
        ok = False
    else:
        logger.info("Team name: %s", TEAM_NAME)

    return ok


def parse_sandbox_args() -> tuple[str, str, str]:
    """
    Parse optional sandbox CLI flags:
      --topic  "Your debate topic here"
      --pros   team_name_arguing_pro
      --cons   team_name_arguing_con

    Only used when running with --sandbox. Has zero effect on live match runs.
    """
    topic, pros, cons = "", "", ""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--topic" and i + 1 < len(args):
            topic = args[i + 1]
        elif arg == "--pros" and i + 1 < len(args):
            pros = args[i + 1]
        elif arg == "--cons" and i + 1 < len(args):
            cons = args[i + 1]
    return topic, pros, cons


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

    # ── SANDBOX ONLY: pre-fill topic & stance from CLI flags ──────────────────
    # This block is skipped entirely during live match (no --sandbox flag).
    # Usage:
    #   python agent.py --sandbox --topic "AI in finance" --pros team2 --cons Perplexity
    #   python agent.py --sandbox --topic "AI in finance" --pros Perplexity --cons team2
    if sandbox:
        topic, pros, cons = parse_sandbox_args()
        if topic:
            state.topic = topic
            state.pros = pros if pros else TEAM_NAME
            state.cons = cons if cons else "Perplexity"
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info("  SANDBOX PRE-FILL")
            logger.info("  Topic : %s", state.topic)
            logger.info("  PRO   : %s", state.pros)
            logger.info("  CON   : %s", state.cons)
            logger.info("  Stance: %s", state.our_stance)
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        else:
            logger.warning(
                "Sandbox mode: no --topic given. "
                "Agent will respond but prompts will have empty topic. "
                "Tip: python agent.py --sandbox --topic \"...\" --pros team2 --cons Perplexity"
            )
    # ─────────────────────────────────────────────────────────────────────────

    no_search = "--no-search" in sys.argv
    engine = DebateEngine(use_web_search=not no_search)
    if no_search:
        logger.info("Web search DISABLED (--no-search flag). Using reasoning only.")
    client = WSClient(ws_url, state, engine, sandbox=sandbox)

    try:
        await client.connect()
    except KeyboardInterrupt:
        logger.info("Interrupted by user — shutting down")
        client.stop()
    except Exception as e:
        logger.critical("Unhandled exception: %s", e)
        client.stop()
    finally:
        logger.info(engine.usage_summary())
        logger.info("Agent shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
