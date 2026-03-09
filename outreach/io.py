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
    """Encapsulates I/O operations and an asynchronous queue for a single CSV file."""
    def __init__(self, path: Path):
        self.path = path
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker())
        self._existing_keys = set()
        self._load_existing_keys()

    def _load_existing_keys(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cs = row.get("City/State", "")
                    school = row.get("School Name", "").strip()
                    faculty = row.get("Faculty Name", "").strip()
                    if cs and school and faculty:
                        self._existing_keys.add((cs, school, faculty))
        except Exception as e:
            print(f"[WARN] Error reading {self.path.name} for deduplication: {e}")

    async def _worker(self):
        """Background task that continually processes the write queue."""
        while True:
            rows = await self._queue.get()
            if rows is None:  # Shutdown signal
                self._queue.task_done()
                break
                
            try:
                def _write():
                    file_exists = self.path.exists()
                    with open(self.path, "a", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
                        if not file_exists or self.path.stat().st_size == 0:
                            writer.writeheader()
                        writer.writerows(rows)
                await asyncio.to_thread(_write)
                print(f"Appended {len(rows)} rows to {self.path.name}")
            except Exception as e:
                print(f"[ERROR] Failed to write to {self.path.name}: {e}")
            finally:
                self._queue.task_done()

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
        """Asynchronously enqueue rows to be appended to the output CSV."""
        if not rows:
            return
            
        unique_rows = []
        for row in rows:
            cs = row.get("City/State", "")
            school_name = row.get("School Name", "").strip()
            faculty_name = row.get("Faculty Name", "").strip()
            key = (cs, school_name, faculty_name)
            if key not in self._existing_keys:
                unique_rows.append(row)
                self._existing_keys.add(key)
                
        if not unique_rows:
            return
            
        await self._queue.put(unique_rows)
        
    async def shutdown(self):
        """Signal the worker to flush all remaining items and stop."""
        await self._queue.put(None)
        await self._worker_task


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
