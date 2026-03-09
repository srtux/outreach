"""
School Outreach Research Agent
Uses Google ADK with Gemini + Google Search to find school faculty contacts.
"""

import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.google_search_agent_tool import create_google_search_agent, GoogleSearchAgentTool
from google.adk.tools.load_web_page import load_web_page
from google.genai import types

# Add project root to sys.path to allow 'src' package imports when running as a script
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import SchoolContact, SchoolSearchResult
from src.prompts import STUDENTS_SYSTEM_PROMPT, VOLUNTEERS_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# GLOBAL CONFIGURATION
# ---------------------------------------------------------------------------
# File system paths
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_CSV = DATA_DIR / "regions.csv"
OUTPUT_STUDENTS = DATA_DIR / "students.csv"
OUTPUT_VOLUNTEERS = DATA_DIR / "volunteers.csv"

MODEL_ID = "gemini-3-flash-preview"

STUDENTS_TARGET = 20  # elementary/middle school contacts per city
VOLUNTEERS_TARGET = 20  # high school CS contacts per city


# Retry settings for 429 rate-limit errors
MAX_RETRIES = 5
RETRY_BASE_DELAY = 15.0  # seconds; doubles each retry

# Concurrency settings
MAX_CONCURRENT_CITIES = 3  # number of cities to research in parallel
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

# ---------------------------------------------------------------------------
# AGENT BUILDERS
# ---------------------------------------------------------------------------


def build_agent(agent_type: str) -> LlmAgent:
    """
    Create and configure an LlmAgent for specified research tasks.

    Args:
        agent_type: Either 'students' or 'volunteers' to determine the instructions.

    Returns:
        A configured LlmAgent instance.
    """
    if agent_type == "students":
        instruction = STUDENTS_SYSTEM_PROMPT.format(target=STUDENTS_TARGET)
        name = "students_researcher"
    else:
        instruction = VOLUNTEERS_SYSTEM_PROMPT.format(target=VOLUNTEERS_TARGET)
        name = "volunteers_researcher"

    # search_agent is a lightweight agent specifically for finding search terms and processing SERP results
    search_agent = create_google_search_agent("gemini-2.0-flash")
    search_agent_tool = GoogleSearchAgentTool(agent=search_agent)

    return LlmAgent(
        name=name,
        model=MODEL_ID,
        instruction=instruction,
        tools=[search_agent_tool, load_web_page],
        output_schema=SchoolSearchResult,
    )


# ---------------------------------------------------------------------------
# CORE SEARCH AND RESPONSE PARSING
# ---------------------------------------------------------------------------


def parse_agent_response(text: str) -> list[SchoolContact]:
    """Robustly extract and parse contact information from the agent's response using json-repair."""
    import json_repair
    
    try:
        data = json_repair.loads(text)
        # 1. Try bulk validation first (fastest)
        try:
            return SchoolSearchResult.model_validate(data).contacts
        except Exception:
            # 2. Fallback: Parse individually to be resilient to single bad items
            raw = data.get("contacts", data) if isinstance(data, dict) else data
            if not isinstance(raw, list): return []
            
            contacts = []
            for item in raw:
                try: contacts.append(SchoolContact.model_validate(item))
                except Exception: continue
            return contacts
    except Exception:
        return []


async def _run_agent_once(
    runner: Runner,
    city: str,
    state: str,
    session_service: InMemorySessionService,
    existing_counts: dict[str, int] | None = None,
) -> list[SchoolContact]:
    """Single attempt to run the agent for a city."""
    session = await session_service.create_session(
        app_name=runner.app_name, user_id="user"
    )

    prompt_text = f"Find school contacts in {city}, {state}."
    if existing_counts:
        prompt_text += "\n\nAlready researched schools for this area:\n"
        for school, count in existing_counts.items():
            prompt_text += f"- {school}: {count} contacts found\n"
        prompt_text += "\nSkip schools that already have 2 or more contacts."
        prompt_text += " Retry schools with 0 or 1 contacts to try to find more."
        prompt_text += " Focus your search on finding NEW schools not listed above."

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=prompt_text)],
    )

    agent_icon = "🎓" if "student" in runner.agent.name else "🤝"
    agent_label = f"{agent_icon} {runner.agent.name.replace('_researcher', '').title():<10}"

    collected_text = ""
    print(f"  {agent_label} | 🚀 Starting research...")

    async for event in runner.run_async(
        user_id="user",
        session_id=session.id,
        new_message=user_msg,
    ):
        if hasattr(event, "get_function_calls"):
            for call in event.get_function_calls():
                args = call.args or {}
                if "search" in call.name.lower():
                    query = args.get("query", args.get("q", args.get("request", "unknown query")))
                    print(f"  {agent_label} | 🔍 Searching Google: '{query}'")
                elif "load" in call.name.lower() or "page" in call.name.lower():
                    url = args.get("url", "unknown url")
                    print(f"  {agent_label} | 🌐 Scraping website: {url}")
                else:
                    print(f"  {agent_label} | 🛠️ Using tool: {call.name}")

        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    collected_text += part.text

    contacts = parse_agent_response(collected_text)
    if contacts:
        print(f"\n  {agent_label} | ✅ Successfully found {len(contacts)} contacts for {city}, {state}:")
        for c in contacts:
            print(f"     ➔ 🏫 {c.school_name}")
            print(f"        👤 {c.faculty_name}  |  💼 {c.comments or 'No title'}")
            print(f"        📧 {c.email or 'No email found'}")
        print()
    else:
        print(f"\n  {agent_label} | ⚠️ No contacts parsed for {city}, {state}")

    return contacts


async def search_city(
    runner: Runner,
    city: str,
    state: str,
    session_service: InMemorySessionService,
    existing_counts: dict[str, int] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[SchoolContact]:
    """Run the agent for a city with automatic retry on rate-limit errors.

    When a semaphore is provided, it limits how many cities run concurrently
    to avoid overwhelming the API with too many parallel requests.
    """
    async def _inner() -> list[SchoolContact]:
        for attempt in range(MAX_RETRIES):
            try:
                return await asyncio.wait_for(
                    _run_agent_once(runner, city, state, session_service, existing_counts),
                    timeout=AGENT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"  [TIMEOUT] Agent timed out for {city}, {state} "
                      f"(attempt {attempt + 1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
                else:
                    return []
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_BASE_DELAY * (2 ** attempt)
                        print(f"  [RATE LIMITED] Attempt {attempt + 1}/{MAX_RETRIES}. "
                              f"Waiting {delay:.0f}s before retrying...")
                        await asyncio.sleep(delay)
                    else:
                        raise
                else:
                    raise
        return []

    if semaphore is not None:
        async with semaphore:
            return await _inner()
    return await _inner()


# ---------------------------------------------------------------------------
# DATASET I/O (CSV)
# ---------------------------------------------------------------------------

def read_regions(csv_path: Path) -> list[dict]:
    """Read the Regions CSV and return a list of dicts with keys:
    City, State."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"Loaded {len(rows)} region entries from {csv_path.name}")
    return rows


from collections import defaultdict

def _read_completed_cities(csv_path: Path) -> dict[str, dict[str, int]]:
    """Read an output CSV and return a mapping of 'City|State' -> 'School Name' -> count of contacts."""
    seen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if not csv_path.exists():
        return {}
    
    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
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
        print(f"[WARN] Error reading {csv_path.name}: {e}")
        
    return {k: dict(v) for k, v in seen.items()}


def append_output_csv(
    path: Path,
    rows: list[dict],
) -> None:
    """Append rows to the output CSV."""
    if not rows:
        return
    file_exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        if not file_exists or path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(rows)
    print(f"Appended {len(rows)} rows to {path.name}")


def contact_to_row(
    contact: SchoolContact,
    city: str,
    state: str,
) -> dict:
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


# ---------------------------------------------------------------------------
# ASYNC WORKFLOW EXECUTION
# ---------------------------------------------------------------------------

async def _process_city(
    city: str,
    state: str,
    students_runner: Runner,
    volunteers_runner: Runner,
    session_service: InMemorySessionService,
    semaphore: asyncio.Semaphore,
    csv_lock: asyncio.Lock,
    progress: dict,
    student_counts: dict[str, int],
    volunteer_counts: dict[str, int],
) -> None:
    """Process a single city: run both agents, write results to CSV."""
    idx = progress["done"] + 1
    total = progress["total"]

    print(f"\n" + "━"*70)
    print(f" 📍 [{idx}/{total}] RESEARCHING: {city}, {state} ".center(70, "━"))
    print(f"━"*70)
    print(f"\n🎯 Target: {STUDENTS_TARGET} elementary/middle + {VOLUNTEERS_TARGET} high school CS contacts")
    print(f"⚡ Running agents concurrently...\n")

    start = time.monotonic()

    results = await asyncio.gather(
        search_city(students_runner, city, state, session_service, student_counts, semaphore),
        search_city(volunteers_runner, city, state, session_service, volunteer_counts, semaphore),
        return_exceptions=True,
    )

    elapsed = time.monotonic() - start

    student_rows: list[dict] = []
    volunteer_rows: list[dict] = []

    # Process Students
    student_res = results[0]
    if isinstance(student_res, Exception):
        print(f"  ❌ [ERROR] Students search failed for {city}, {state}: {student_res}")
    else:
        for c in student_res:
            student_rows.append(contact_to_row(c, city, state))

    # Process Volunteers
    volunteer_res = results[1]
    if isinstance(volunteer_res, Exception):
        print(f"  ❌ [ERROR] Volunteers search failed for {city}, {state}: {volunteer_res}")
    else:
        for c in volunteer_res:
            volunteer_rows.append(contact_to_row(c, city, state))

    # Serialize CSV writes to avoid interleaved output
    async with csv_lock:
        append_output_csv(OUTPUT_STUDENTS, student_rows)
        append_output_csv(OUTPUT_VOLUNTEERS, volunteer_rows)

    progress["done"] += 1
    print(f"\n  ⏱️ {city}, {state} completed in {elapsed:.1f}s "
          f"({len(student_rows)} students, {len(volunteer_rows)} volunteers) "
          f"[{progress['done']}/{total} cities done]")


async def main() -> None:
    """
    Orchestrates the research process for all regions.

    Workflow:
    1. Loads region data from CSV.
    2. Initializes LLM runners for student and volunteer research.
    3. Filters out already-processed cities based on output files.
    4. Concurrently processes pending cities using a semaphore to manage API load.
    """
    if not REGIONS_CSV.exists():
        print(f"ERROR: Regions CSV not found at {REGIONS_CSV}")
        sys.exit(1)

    # Verify API key
    if not os.environ.get("GOOGLE_API_KEY"):
        print(
            "ERROR: Set the GOOGLE_API_KEY environment variable.\n"
            "  Get one at https://aistudio.google.com/apikey"
        )
        sys.exit(1)

    regions = read_regions(REGIONS_CSV)

    # Build agents
    students_agent = build_agent("students")
    volunteers_agent = build_agent("volunteers")

    session_service = InMemorySessionService()

    students_runner = Runner(
        agent=students_agent,
        app_name="outreach",
        session_service=session_service,
    )
    volunteers_runner = Runner(
        agent=volunteers_agent,
        app_name="outreach",
        session_service=session_service,
    )

    # Deduplicate already-processed cities to allow resumption after failure
    seen_students = _read_completed_cities(OUTPUT_STUDENTS)
    seen_volunteers = _read_completed_cities(OUTPUT_VOLUNTEERS)

    # Filter to pending regions
    pending: list[tuple[str, str, dict[str, int], dict[str, int]]] = []
    for region in regions:
        city = region.get("City", "").strip()
        state = region.get("State", "").strip()

        if not city or not state:
            print(f"[SKIP] Missing city/state in row: {region}")
            continue

        city_key = f"{city}|{state}"
        student_counts = seen_students.get(city_key, {})
        volunteer_counts = seen_volunteers.get(city_key, {})
        
        # Skip city entirely if we've already found enough schools
        if len(student_counts) >= STUDENTS_TARGET and len(volunteer_counts) >= VOLUNTEERS_TARGET:
            print(f"[SKIP] {city}, {state} already has enough schools ({len(student_counts)} students, {len(volunteer_counts)} volunteers).")
            continue
            
        pending.append((city, state, student_counts, volunteer_counts))

    if not pending:
        print("No new cities to process.")
        return

    print(f"\n📋 {len(pending)} cities to process (max {MAX_CONCURRENT_CITIES} concurrently)")

    # Semaphore limits concurrent API calls to prevent 429 errors from Google Gemini
    # csv_lock ensures multiple workers don't corrupt output files
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CITIES)
    csv_lock = asyncio.Lock()
    progress = {"done": 0, "total": len(pending)}

    overall_start = time.monotonic()

    # Launch all cities concurrently — the semaphore gates actual execution
    tasks = [
        _process_city(
            city, state,
            students_runner, volunteers_runner,
            session_service, semaphore, csv_lock, progress,
            student_counts, volunteer_counts,
        )
        for city, state, student_counts, volunteer_counts in pending
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    overall_elapsed = time.monotonic() - overall_start

    print(f"\n" + "━"*70)
    print(f"🎉 All {len(pending)} regions processed in {overall_elapsed:.1f}s!".center(70))
    print(f"━"*70 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Process interrupted by user. Exiting gracefully...")
        sys.exit(0)
