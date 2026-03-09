"""
School Outreach Research Agent
Uses Google ADK with Gemini + Google Search to find school faculty contacts.
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from outreach.config import (
    REGIONS_CSV, OUTPUT_STUDENTS, OUTPUT_VOLUNTEERS,
    STUDENTS_TARGET, VOLUNTEERS_TARGET,
    MIN_SCHOOLS_TARGET, MIN_CONTACTS_TARGET,
    MAX_CONCURRENT_CITIES
)
from outreach.io import read_regions, CsvRepository
from outreach.agents import build_agent
from outreach.search import search_city

@dataclass
class ResearchApp:
    """Encapsulates the global state and dependencies for the research application."""
    session_service: InMemorySessionService
    semaphore: asyncio.Semaphore
    students_runner: Runner
    volunteers_runner: Runner
    students_repo: CsvRepository
    volunteers_repo: CsvRepository
    progress: dict[str, int]


async def _process_city(
    app: ResearchApp,
    city: str,
    state: str,
    student_counts: dict[str, int],
    volunteer_counts: dict[str, int],
) -> None:
    """Process a single city: run both agents using the app context."""
    idx = app.progress["done"] + 1
    total = app.progress["total"]

    print(f"\n" + "═"*75)
    header = f" 📍 [{idx}/{total}] RESEARCHING: {city}, {state} "
    print(f"{header:^75}")
    print(f"═"*75)
    print(f"🎯 Target: {STUDENTS_TARGET} elementary/middle + {VOLUNTEERS_TARGET} high school CS contacts")
    print(f"⚡ Running agents concurrently...")

    start = time.monotonic()

    results = await asyncio.gather(
        search_city(app.students_runner, city, state, app.session_service, app.students_repo, student_counts, app.semaphore),
        search_city(app.volunteers_runner, city, state, app.session_service, app.volunteers_repo, volunteer_counts, app.semaphore),
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

    app.progress["done"] += 1
    print(f"\n  ⏱️ {city}, {state} completed in {elapsed:.1f}s "
          f"({student_count} students, {volunteer_count} volunteers) "
          f"[{app.progress['done']}/{total} cities done]")


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

    students_repo = CsvRepository(OUTPUT_STUDENTS)
    volunteers_repo = CsvRepository(OUTPUT_VOLUNTEERS)

    # Deduplicate already-processed cities to allow resumption after failure
    seen_students = students_repo.get_completed_cities()
    seen_volunteers = volunteers_repo.get_completed_cities()

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
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CITIES)
    progress = {"done": 0, "total": len(pending)}

    app = ResearchApp(
        session_service=session_service,
        semaphore=semaphore,
        students_runner=students_runner,
        volunteers_runner=volunteers_runner,
        students_repo=students_repo,
        volunteers_repo=volunteers_repo,
        progress=progress
    )

    overall_start = time.monotonic()

    # Launch all cities concurrently — the semaphore gates actual execution
    tasks = [
        _process_city(
            app, city, state, student_counts, volunteer_counts,
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
