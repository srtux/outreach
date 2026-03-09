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
from google.adk.tools.google_search_agent_tool import create_google_search_agent, GoogleSearchAgentTool
from google.adk.tools.load_web_page import load_web_page
from google.genai import types

# Add project root to sys.path to allow 'src' package imports when running as a script
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import SchoolContact

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_CSV = DATA_DIR / "regions.csv"
OUTPUT_STUDENTS = DATA_DIR / "students.csv"
OUTPUT_VOLUNTEERS = DATA_DIR / "volunteers.csv"

MODEL_ID = "gemini-3-flash-preview"

STUDENTS_TARGET = 10  # elementary/middle school contacts per city
VOLUNTEERS_TARGET = 12  # high school CS contacts per city


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

Given a city and state:
1. Use the `google_search_agent` tool to find the top {target} elementary and middle schools in that area.
2. For each school, find a faculty contact — preferably the Principal, Vice-Principal, STEM Coordinator, or Technology Teacher.
3. CRITICAL: Do NOT guess or hallucinate email addresses. You MUST use the `load_web_page` tool to browse the school's or district's "Staff", "Directory", or "About Us" page.
4. Extract their precise professional email address from the page text.

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
- If you use `load_web_page` and cannot find an email, leave the field as an empty string. Never make up an email address.
- Always include the school website URL in school_link if available.
- Target exactly {target} contacts.
"""

VOLUNTEERS_SYSTEM_PROMPT = """\
You are a research assistant that finds high school Computer Science teacher \
contacts for a coding camp volunteer recruitment program.

Given a city and state:
1. Use the `google_search_agent` tool to find the top {target} high schools in that area.
2. For each school, find a CS/Computer Science teacher, Robotics coach, Technology instructor, or CTE (Career and Technical Education) coordinator.
3. CRITICAL: Do NOT guess or hallucinate email addresses. You MUST use the `load_web_page` tool to browse the school's or district's "Staff", "Directory", or "About Us" page.
4. Extract their precise professional email address from the page text.

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
- If you use `load_web_page` and cannot find an email, leave the field as an empty string. Never make up an email address.
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

    search_agent = create_google_search_agent("gemini-2.0-flash")
    search_agent_tool = GoogleSearchAgentTool(agent=search_agent)

    return LlmAgent(
        name=name,
        model=MODEL_ID,
        instruction=instruction,
        tools=[search_agent_tool, load_web_page],
    )


# ---------------------------------------------------------------------------
# Core search logic
# ---------------------------------------------------------------------------

def parse_agent_response(text: str) -> list[SchoolContact]:
    """Parse the agent's JSON response into a list of SchoolContact."""
    import re
    # 1. Try to extract from markdown fences
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    else:
        # 2. Try to find the first '{' and last '}' (for dict)
        start_dict = text.find('{')
        end_dict = text.rfind('}')
        # 3. Try to find the first '[' and last ']' (for list)
        start_list = text.find('[')
        end_list = text.rfind(']')

        # Determine which one appears first and is larger
        if start_dict != -1 and (start_list == -1 or start_dict < start_list):
            cleaned = text[start_dict : end_dict + 1]
        elif start_list != -1:
            cleaned = text[start_list : end_list + 1]
        else:
            cleaned = text.strip()

    if not cleaned:
        return []

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"  [WARN] Could not parse JSON from agent response. Skipping.")
        # Logging first 100 chars for debugging
        snippet = (text[:100] + '...') if len(text) > 100 else text
        print(f"  [DEBUG] Response snippet: {repr(snippet)}")
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
        app_name=runner.app_name, user_id="user"
    )

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=f"Find school contacts in {city}, {state}.")],
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
            
    return contacts


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

    # Deduplicate cities
    seen_cities: set[str] = set()

    for path in [OUTPUT_STUDENTS, OUTPUT_VOLUNTEERS]:
        if path.exists():
            try:
                with open(path, "r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        cs = row.get("City/State", "")
                        if cs:
                            parts = [p.strip() for p in cs.split(",", 1)]
                            if len(parts) == 2:
                                seen_cities.add(f"{parts[0]}|{parts[1]}")
            except Exception as e:
                print(f"[WARN] Error reading {path.name}: {e}")

    for region in regions:
        student_rows: list[dict] = []
        volunteer_rows: list[dict] = []

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

        print(f"\n" + "━"*70)
        print(f" 📍 RESEARCHING LOCATION: {city}, {state} ".center(70, "━"))
        print(f"━"*70)

        # --- Concurrent Search ---
        print(f"\n🎯 Target: {STUDENTS_TARGET} elementary/middle contacts and {VOLUNTEERS_TARGET} high school CS contacts")
        print(f"⚡ Running agents concurrently...\n")
        
        results = await asyncio.gather(
            search_city(students_runner, city, state, session_service),
            search_city(volunteers_runner, city, state, session_service),
            return_exceptions=True
        )

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

        append_output_csv(OUTPUT_STUDENTS, student_rows)
        append_output_csv(OUTPUT_VOLUNTEERS, volunteer_rows)

    # Write outputs
    print(f"\n" + "━"*70)
    print(f"🎉 All regions processed successfully!".center(70))
    print(f"━"*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
