import asyncio
from pathlib import Path

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from outreach.models import SchoolSearchResult, SchoolContact
from outreach.config import (
    STUDENTS_TARGET, VOLUNTEERS_TARGET,
    MAX_RETRIES, RETRY_BASE_DELAY, AGENT_TIMEOUT
)
from outreach.io import contact_to_row, CsvRepository

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
    repo: CsvRepository,
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
                await repo.append_rows(rows_to_save)
                
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
    repo: CsvRepository,
    existing_counts: dict[str, int] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[SchoolContact]:
    """Run the agent for a city with automatic retry on rate-limit errors."""
    for attempt in range(MAX_RETRIES):
        try:
            coro = _run_agent_once(runner, city, state, session_service, repo, existing_counts)
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
