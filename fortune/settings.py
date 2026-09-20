import os
import unicodedata
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TOKEN = os.getenv("DISCORD_TOKEN", os.getenv("TOKEN", ""))
OWNER_IDS = {
    int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()
}
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data" / "fortune"))).resolve()
DEFAULT_PREFIX = os.getenv("DEFAULT_PREFIX", ".")
if not 1 <= len(DEFAULT_PREFIX) <= 8 or any(
    c.isspace() or unicodedata.category(c).startswith("C") for c in DEFAULT_PREFIX
):
    raise ValueError("DEFAULT_PREFIX must contain 1–8 visible characters without spaces.")
LOGO_URL = ""
BANNER_URL = ""
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip("/")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))
