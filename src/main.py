"""
School Outreach Research Agent
Uses Google ADK with Gemini + Google Search to find school faculty contacts.
"""

import asyncio
import csv
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from google.genai import types

from models import SchoolContact

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_CSV = DATA_DIR / "2026 Camp Outreach Doc - Regions.csv"
OUTPUT_STUDENTS = DATA_DIR / "outreach_results_students.csv"
OUTPUT_VOLUNTEERS = DATA_DIR / "outreach_results_volunteers.csv"

MODEL_ID = "gemini-3-flash-preview"

STUDENTS_TARGET = 10  # elementary/middle school contacts per city
VOLUNTEERS_TARGET = 12  # high school CS contacts per city

# Rate-limiting: seconds to wait between agent calls
RATE_LIMIT_DELAY = 5.0

# Retry settings for 429 rate-limit errors
MAX_RETRIES = 5
RETRY_BASE_DELAY = 15.0  # seconds; doubles each retry

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
# System prompts
# ---------------------------------------------------------------------------
STUDENTS_SYSTEM_PROMPT = """\
You are a research assistant that finds elementary and middle school contacts \
for a coding camp outreach program.

Given a city and state, use Google Search to:
1. Find the top {target} elementary and middle schools in that area.
2. For each school, search for a faculty contact — preferably the Principal, \
Vice-Principal, STEM Coordinator, or Technology Teacher.
3. Try to find their professional email address on the school or district website.

Return your results as strictly valid JSON matching this schema:
{{
  "contacts": [
    {{
      "school_name": "string",
      "school_link": "string (URL)",
      "faculty_name": "string",
      "email": "string",
      "dear_line": "string (e.g. Dear Mr. Smith)",
      "comments": "string (job title)"
    }}
  ]
}}

IMPORTANT RULES:
- Return ONLY the raw JSON object. No markdown, no code fences, no commentary.
- If you cannot find an email, leave the field as an empty string.
- Always include the school website URL in school_link if available.
- Target exactly {target} contacts.
"""

VOLUNTEERS_SYSTEM_PROMPT = """\
You are a research assistant that finds high school Computer Science teacher \
contacts for a coding camp volunteer recruitment program.

Given a city and state, use Google Search to:
1. Find the top {target} high schools in that area.
2. For each school, search for a CS/Computer Science teacher, Robotics coach, \
Technology instructor, or CTE (Career and Technical Education) coordinator.
3. Try to find their professional email address on the school or district website.

Return your results as strictly valid JSON matching this schema:
{{
  "contacts": [
    {{
      "school_name": "string",
      "school_link": "string (URL)",
      "faculty_name": "string",
      "email": "string",
      "dear_line": "string (e.g. Dear Ms. Jones)",
      "comments": "string (job title)"
    }}
  ]
}}

IMPORTANT RULES:
- Return ONLY the raw JSON object. No markdown, no code fences, no commentary.
- If you cannot find an email, leave the field as an empty string.
- Always include the school website URL in school_link if available.
- Target exactly {target} contacts.
"""

# ---------------------------------------------------------------------------
# Agent builders
# ---------------------------------------------------------------------------

def build_agent(agent_type: str) -> LlmAgent:
    """Create an LlmAgent configured for either students or volunteers."""
    if agent_type == "students":
        instruction = STUDENTS_SYSTEM_PROMPT.format(target=STUDENTS_TARGET)
        name = "students_researcher"
    else:
        instruction = VOLUNTEERS_SYSTEM_PROMPT.format(target=VOLUNTEERS_TARGET)
        name = "volunteers_researcher"

    return LlmAgent(
        name=name,
        model=MODEL_ID,
        instruction=instruction,
        tools=[google_search],
    )


# ---------------------------------------------------------------------------
# Core search logic
# ---------------------------------------------------------------------------

def parse_agent_response(text: str) -> list[SchoolContact]:
    """Parse the agent's JSON response into a list of SchoolContact."""
    # Strip markdown code fences if the model wraps them despite instructions
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[: cleaned.rfind("```")]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"  [WARN] Could not parse JSON from agent response. Skipping.")
        return []

    # Handle both {"contacts": [...]} and bare [...]
    if isinstance(data, list):
        contacts_raw = data
    elif isinstance(data, dict) and "contacts" in data:
        contacts_raw = data["contacts"]
    else:
        print(f"  [WARN] Unexpected JSON structure. Skipping.")
        return []

    contacts = []
    for item in contacts_raw:
        try:
            contacts.append(SchoolContact(**item))
        except Exception as e:
            print(f"  [WARN] Skipping malformed contact: {e}")
    return contacts


async def _run_agent_once(
    runner: Runner,
    city: str,
    state: str,
    session_service: InMemorySessionService,
) -> list[SchoolContact]:
    """Single attempt to run the agent for a city."""
    session = await session_service.create_session(
        app_name="outreach", user_id="user"
    )

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=f"Find school contacts in {city}, {state}.")],
    )

    collected_text = ""
    async for event in runner.run_async(
        user_id="user",
        session_id=session.id,
        new_message=user_msg,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    collected_text += part.text

    return parse_agent_response(collected_text)


async def search_city(
    runner: Runner,
    city: str,
    state: str,
    session_service: InMemorySessionService,
) -> list[SchoolContact]:
    """Run the agent for a city with automatic retry on rate-limit errors."""
    for attempt in range(MAX_RETRIES):
        try:
            return await _run_agent_once(runner, city, state, session_service)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  [RATE LIMITED] Attempt {attempt + 1}/{MAX_RETRIES}. "
                      f"Waiting {delay:.0f}s before retrying...")
                await asyncio.sleep(delay)
            else:
                raise
    # Final attempt — let it raise if it fails
    return await _run_agent_once(runner, city, state, session_service)


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_regions(csv_path: Path) -> list[dict]:
    """Read the Regions CSV and return a list of dicts with keys:
    City, State."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"Loaded {len(rows)} region entries from {csv_path.name}")
    return rows


def write_output_csv(
    path: Path,
    rows: list[dict],
) -> None:
    """Write the final output CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path.name}")


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
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
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

    student_rows: list[dict] = []
    volunteer_rows: list[dict] = []

    # Deduplicate cities
    seen_cities: set[str] = set()

    for region in regions:
        city = region.get("City", "").strip()
        state = region.get("State", "").strip()

        if not city or not state:
            print(f"[SKIP] Missing city/state in row: {region}")
            continue

        city_key = f"{city}|{state}"
        if city_key in seen_cities:
            print(f"[SKIP] Already processed {city}, {state}")
            continue
        seen_cities.add(city_key)

        print(f"\n{'='*60}")
        print(f"Researching: {city}, {state}")
        print(f"{'='*60}")

        # --- Students search ---
        print(f"  Searching for {STUDENTS_TARGET} elementary/middle contacts...")
        try:
            student_contacts = await search_city(
                students_runner,
                city,
                state,
                session_service,
            )
            print(f"  Found {len(student_contacts)} student contacts.")
            for c in student_contacts:
                student_rows.append(contact_to_row(c, city, state))
        except Exception as e:
            print(f"  [ERROR] Students search failed for {city}, {state}: {e}")

        # Rate limit
        await asyncio.sleep(RATE_LIMIT_DELAY)

        # --- Volunteers search ---
        print(f"  Searching for {VOLUNTEERS_TARGET} high school CS contacts...")
        try:
            volunteer_contacts = await search_city(
                volunteers_runner,
                city,
                state,
                session_service,
            )
            print(f"  Found {len(volunteer_contacts)} volunteer contacts.")
            for c in volunteer_contacts:
                volunteer_rows.append(contact_to_row(c, city, state))
        except Exception as e:
            print(f"  [ERROR] Volunteers search failed for {city}, {state}: {e}")

        # Rate limit between cities
        await asyncio.sleep(RATE_LIMIT_DELAY)

    # Write outputs
    print(f"\n{'='*60}")
    print("Writing output files...")
    print(f"{'='*60}")
    write_output_csv(OUTPUT_STUDENTS, student_rows)
    write_output_csv(OUTPUT_VOLUNTEERS, volunteer_rows)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
