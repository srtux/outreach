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

from outreach.models import SchoolContact, SchoolSearchResult
from outreach.prompts import STUDENTS_SYSTEM_PROMPT, VOLUNTEERS_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# GLOBAL CONFIGURATION
# ---------------------------------------------------------------------------
# File system paths
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_CSV = DATA_DIR / "regions.csv"
OUTPUT_STUDENTS = DATA_DIR / "students.csv"
OUTPUT_VOLUNTEERS = DATA_DIR / "volunteers.csv"

MODEL_ID = os.environ.get("MODEL_ID", "gemini-3-flash-preview")

MIN_SCHOOLS_TARGET = int(os.environ.get("MIN_SCHOOLS_TARGET", "3"))  # minimum unique schools per city
MIN_CONTACTS_TARGET = int(os.environ.get("MIN_CONTACTS_TARGET", "20"))  # minimum total contacts per city

# Backwards compatibility / fallbacks (used for prompting the agent)
STUDENTS_TARGET = int(os.environ.get("STUDENTS_TARGET", str(MIN_CONTACTS_TARGET)))  # elementary/middle school contacts per city
VOLUNTEERS_TARGET = int(os.environ.get("VOLUNTEERS_TARGET", str(MIN_CONTACTS_TARGET)))  # high school CS contacts per city


# Retry settings for 429 rate-limit errors
MAX_RETRIES = 5
RETRY_BASE_DELAY = 15.0  # seconds; doubles each retry

# Concurrency settings
MAX_CONCURRENT_CITIES = int(os.environ.get("MAX_CONCURRENT_CITIES", "15"))  # number of cities to research in parallel
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
    from pydantic import ValidationError
    
    try:
        data = json_repair.loads(text)
        # 1. Try bulk validation first (fastest)
        try:
            return SchoolSearchResult.model_validate(data).contacts
        except ValidationError:
            # 2. Fallback: Parse individually to be resilient to single bad items
            raw = data.get("contacts", data) if isinstance(data, dict) else data
            if not isinstance(raw, list): return []
            
            contacts = []
            for item in raw:
                try: contacts.append(SchoolContact.model_validate(item))
                except ValidationError: continue
            return contacts
    except (ValueError, Exception) as e:
        # Fallback to broad exception just in case json_repair throws something else, 
        # but primarily catch ValueError from json issues
        if not isinstance(e, ValueError) and not type(e).__name__ == 'JSONDecodeError':
            print(f"[WARN] Unexpected error in parse_agent_response: {e}")
        return []


async def _run_agent_once(
    runner: Runner,
    city: str,
    state: str,
    session_service: InMemorySessionService,
    csv_path: Path,
    csv_lock: asyncio.Lock,
    existing_counts: dict[str, int] | None = None,
) -> list[SchoolContact]:
    """Single attempt to run the agent for a city, progressively saving contacts."""
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

    # Determine agent icon and short label
    agent_icon = "🎓" if "student" in runner.agent.name else "🤝"
    agent_short = "Stud." if "student" in runner.agent.name else "Vol."
    
    # Track progress for this city from existing counts
    cur_found = len(existing_counts) if existing_counts else 0
    target = STUDENTS_TARGET if "student" in runner.agent.name else VOLUNTEERS_TARGET
    
    # Compactly include city and progress in label to disambiguate parallel logs
    city_tag = f"{city}, {state}"
    agent_label = f"{agent_icon} {agent_short:<5} | {city_tag:<16} | {cur_found:>2}/{target}"

    collected_text = ""
    print(f"  {agent_label} | 🚀 Starting research...")
    
    saved_contacts_count = 0
    all_contacts: list[SchoolContact] = []

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
                    short_query = (query[:60] + "...") if len(query) > 63 else query
                    print(f"  {agent_label} | 🔍 Google Search    : '{short_query}'")
                elif "load" in call.name.lower() or "page" in call.name.lower():
                    url = args.get("url", "unknown url")
                    short_url = (url[:60] + "...") if len(url) > 63 else url
                    print(f"  {agent_label} | 🌐 Scraping Website : {short_url}")
                else:
                    print(f"  {agent_label} | 🛠️  Using tool     : {call.name}")

        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    collected_text += part.text
                    
            # Progressively parse what we have so far
            current_contacts = parse_agent_response(collected_text)
            
            # Identify purely new contacts that emerged in this chunk
            if len(current_contacts) > saved_contacts_count:
                new_contacts = current_contacts[saved_contacts_count:]
                all_contacts.extend(new_contacts)
                
                rows_to_save = [contact_to_row(c, city, state) for c in new_contacts]
                async with csv_lock:
                    append_output_csv(csv_path, rows_to_save)
                
                saved_contacts_count = len(current_contacts)
                
                # Print the new contacts as they stream in
                for c in new_contacts:
                    school = (c.school_name[:35] + "..") if len(c.school_name) > 37 else c.school_name
                    print(f"  {agent_label} | ✨ Found: 🏫 {school:<37} | 👤 {c.faculty_name}")

    if all_contacts:
        print(f"\n  {agent_label} | ✅ Completed {len(all_contacts)} total contacts for this run.\n")
    else:
        print(f"\n  {agent_label} | ⚠️ No contacts parsed.\n")

    return all_contacts


async def search_city(
    runner: Runner,
    city: str,
    state: str,
    session_service: InMemorySessionService,
    csv_path: Path,
    csv_lock: asyncio.Lock,
    existing_counts: dict[str, int] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[SchoolContact]:
    """Run the agent for a city with automatic retry on rate-limit errors.

    When a semaphore is provided, it limits how many cities run concurrently
    to avoid overwhelming the API with too many parallel requests.
    """
    for attempt in range(MAX_RETRIES):
        try:
            coro = _run_agent_once(runner, city, state, session_service, csv_path, csv_lock, existing_counts)
            if semaphore is not None:
                async with semaphore:
                    return await asyncio.wait_for(coro, timeout=AGENT_TIMEOUT)
            else:
                return await asyncio.wait_for(coro, timeout=AGENT_TIMEOUT)
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
                    print(f"  [RATE LIMITED] {city}, {state} | Attempt {attempt + 1}/{MAX_RETRIES}. "
                          f"Waiting {delay:.0f}s before retrying...")
                    await asyncio.sleep(delay)
                else:
                    raise
            else:
                raise
    return []


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

    print(f"\n" + "═"*75)
    header = f" 📍 [{idx}/{total}] RESEARCHING: {city}, {state} "
    print(f"{header:^75}")
    print(f"═"*75)
    print(f"🎯 Target: {STUDENTS_TARGET} elementary/middle + {VOLUNTEERS_TARGET} high school CS contacts")
    print(f"⚡ Running agents concurrently...")

    start = time.monotonic()

    results = await asyncio.gather(
        search_city(students_runner, city, state, session_service, OUTPUT_STUDENTS, csv_lock, student_counts, semaphore),
        search_city(volunteers_runner, city, state, session_service, OUTPUT_VOLUNTEERS, csv_lock, volunteer_counts, semaphore),
        return_exceptions=True,
    )

    elapsed = time.monotonic() - start

    student_res = results[0]
    volunteer_res = results[1]

    # Process Students
    if isinstance(student_res, Exception):
        print(f"  ❌ [ERROR] Students search failed for {city}, {state}: {student_res}")
        student_count = 0
    else:
        student_count = len(student_res)

    # Process Volunteers
    if isinstance(volunteer_res, Exception):
        print(f"  ❌ [ERROR] Volunteers search failed for {city}, {state}: {volunteer_res}")
        volunteer_count = 0
    else:
        volunteer_count = len(volunteer_res)

    progress["done"] += 1
    print(f"\n  ⏱️ {city}, {state} completed in {elapsed:.1f}s "
          f"({student_count} students, {volunteer_count} volunteers) "
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
        
        # Evaluate targets based on MIN requirements
        total_student_schools = len(student_counts)
        total_student_contacts = sum(student_counts.values()) if student_counts else 0
        
        total_volunteer_schools = len(volunteer_counts)
        total_volunteer_contacts = sum(volunteer_counts.values()) if volunteer_counts else 0
        
        student_done = (total_student_schools >= MIN_SCHOOLS_TARGET) and \
                       (total_student_contacts >= MIN_CONTACTS_TARGET)
                       
        volunteer_done = (total_volunteer_schools >= MIN_SCHOOLS_TARGET) and \
                         (total_volunteer_contacts >= MIN_CONTACTS_TARGET)
        
        if student_done and volunteer_done:
            print(f"[SKIP] {city}, {state} already meets all targets "
                  f"({total_student_schools}sch/{total_student_contacts}c stud, "
                  f"{total_volunteer_schools}sch/{total_volunteer_contacts}c vol).")
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


def main_cli():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Process interrupted by user. Exiting gracefully...")
        sys.exit(0)

if __name__ == "__main__":
    main_cli()
