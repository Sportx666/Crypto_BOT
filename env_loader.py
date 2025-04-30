# crypto_bot/env_loader.py
"""Load environment variables from a .env file located at project root."""
from pathlib import Path
from dotenv import load_dotenv

def load_env() -> None:
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)
