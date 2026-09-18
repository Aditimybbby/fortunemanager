import os
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
LOGO_URL = os.getenv(
    "BRAND_LOGO_URL",
    "https://cdn.discordapp.com/attachments/1548357273607475330/1549018905916215469/f1fb85df-3216-4f48-8849-154ba7452d8b.png?ex=6aab2606&is=6aa9d486&hm=bc90300d74daadad60d36602dbfd46c05c86646c1ba42626ba4db359b6b44f03&",
)
BANNER_URL = os.getenv(
    "BRAND_BANNER_URL",
    "https://cdn.discordapp.com/attachments/1548357273607475330/1549018906486636574/file_00000000c3dc82118f6860980518ecdd.png?ex=6aab2606&is=6aa9d486&hm=114f017cfbfef3ae0b177b97f90280dbc8f6e8e6a9437616dc6c18c44c72cee4&",
)
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip("/")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))
