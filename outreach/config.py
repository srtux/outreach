import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# File system paths
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_CSV = DATA_DIR / "regions.csv"
OUTPUT_STUDENTS = DATA_DIR / "students.csv"
OUTPUT_VOLUNTEERS = DATA_DIR / "volunteers.csv"

MODEL_ID = os.environ.get("MODEL_ID", "gemini-3-flash-preview")

MIN_SCHOOLS_TARGET = int(os.environ.get("MIN_SCHOOLS_TARGET", "3"))
MIN_CONTACTS_TARGET = int(os.environ.get("MIN_CONTACTS_TARGET", "20"))

# Backwards compatibility / fallbacks (used for prompting the agent)
STUDENTS_TARGET = int(os.environ.get("STUDENTS_TARGET", str(MIN_CONTACTS_TARGET)))
VOLUNTEERS_TARGET = int(os.environ.get("VOLUNTEERS_TARGET", str(MIN_CONTACTS_TARGET)))

# Retry settings for 429 rate-limit errors
MAX_RETRIES = 5
RETRY_BASE_DELAY = 15.0  # seconds; doubles each retry

# Concurrency settings
MAX_CONCURRENT_CITIES = int(os.environ.get("MAX_CONCURRENT_CITIES", "15"))
AGENT_TIMEOUT = 300  # seconds; max time for a single agent run

# Output CSV columns
OUTPUT_COLUMNS = [
    "City/State",
    "School Name",
    "School Link",
    "Faculty Name",
    "Email",
    "Dear Line",
    "Comments",
]
