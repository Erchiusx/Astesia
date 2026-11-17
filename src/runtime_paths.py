"""Separate immutable application code from private config and mutable state."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

CODE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get('PERSONAL_AGENT_CONFIG_DIR', Path.home()/'.config/personal-agent')).expanduser().resolve()
DATA_DIR = Path(os.environ.get('PERSONAL_AGENT_DATA_DIR', Path.home()/'.local/share/personal-agent')).expanduser().resolve()
TIMEZONE = ZoneInfo(os.environ.get('PERSONAL_AGENT_TIMEZONE', 'UTC'))
