import os
from dotenv import load_dotenv

load_dotenv()

# Environment variables
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
WS_URL = os.getenv("WS_URL", "")
SANDBOX_WS_URL = os.getenv("SANDBOX_WS_URL", "")
TEAM_EMAIL = os.getenv("TEAM_EMAIL", "")
TEAM_PASSWORD = os.getenv("TEAM_PASSWORD", "")
TEAM_NAME = os.getenv("TEAM_NAME", "")

# Message limits
MAX_MESSAGE_CHARS = 3000
SAFE_MESSAGE_CHARS = 2800

# Timing
RESPONSE_DEADLINE_SECONDS = 90
RECONNECT_WINDOW_SECONDS = 100
MAX_RECONNECT_ATTEMPTS = 5

# AI model
MODEL = "claude-sonnet-4-5-20250514"
MAX_TOKENS = 900
CONVERSATION_HISTORY_LIMIT = 8
