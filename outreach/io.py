import asyncio
import csv
from collections import defaultdict
from pathlib import Path

from outreach.models import SchoolContact
from outreach.config import OUTPUT_COLUMNS

def read_regions(csv_path: Path) -> list[dict]:
    """Read the Regions CSV and return a list of dicts with keys: City, State."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"Loaded {len(rows)} region entries from {csv_path.name}")
    return rows


class CsvRepository:
    """Encapsulates I/O operations and asynchronous locking for a single CSV file."""
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    def get_completed_cities(self) -> dict[str, dict[str, int]]:
        """Read the output CSV and return a mapping of 'City|State' -> 'School Name' -> count of contacts."""
        seen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        if not self.path.exists():
            return {}
        
        try:
            with open(self.path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cs = row.get("City/State", "")
                    school_name = row.get("School Name", "").strip()
                    if cs and school_name:
                        parts = [p.strip() for p in cs.split(",", 1)]
                        if len(parts) == 2:
                            city_key = f"{parts[0]}|{parts[1]}"
                            seen[city_key][school_name] += 1
        except Exception as e:
            print(f"[WARN] Error reading {self.path.name}: {e}")
            
        return {k: dict(v) for k, v in seen.items()}

    async def append_rows(self, rows: list[dict]) -> None:
        """Asynchronously append rows to the output CSV while holding the lock."""
        if not rows:
            return
            
        async with self._lock:
            def _write():
                file_exists = self.path.exists()
                with open(self.path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
                    if not file_exists or self.path.stat().st_size == 0:
                        writer.writeheader()
                    writer.writerows(rows)
            await asyncio.to_thread(_write)
        print(f"Appended {len(rows)} rows to {self.path.name}")


def contact_to_row(contact: SchoolContact, city: str, state: str) -> dict:
    """Convert a SchoolContact into a CSV row dict."""
    return {
        "City/State": f"{city}, {state}",
        "School Name": contact.school_name,
        "School Link": contact.school_link,
        "Faculty Name": contact.faculty_name,
        "Email": contact.email,
        "Dear Line": contact.dear_line,
        "Comments": contact.comments,
    }
