# Agent Architecture Documentation

## How the Agent Works

### Overview

The School Outreach Research Agent is an autonomous web research system that finds faculty contact information at schools for educational outreach programs. It uses Google's Agent Development Kit (ADK) with Gemini LLMs to search the web, scrape school websites, and extract verified contact data.

### Core Components

#### 1. Orchestrator (`outreach/main.py` — `main()`)

The orchestrator is the entry point. It:

1. Loads target cities from `data/regions.csv`
2. Skips cities already present in the output CSVs (deduplication)
3. For each city, launches **two agents concurrently** via `asyncio.gather()`:
   - **Students Agent** — finds elementary/middle school contacts
   - **Volunteers Agent** — finds high school CS teacher contacts
4. Validates and appends results to `data/students.csv` and `data/volunteers.csv`

#### 2. Agent Construction (`build_agent()`)

Each agent is a `google.adk.agents.LlmAgent` configured with:

- **Model**: `gemini-3-flash-preview` (primary reasoning engine)
- **System prompt**: Detailed instructions for the research task, target count, JSON schema, and anti-hallucination rules
- **Tools**:
  - `GoogleSearchAgentTool` — a sub-agent wrapping the same `MODEL_ID` for Google Search
  - `load_web_page` — a Python function that fetches HTML via `requests` and parses it with BeautifulSoup

#### 3. Execution Flow (`_run_agent_once()`)

For a single city + agent type:

1. Creates an in-memory session
2. Sends the user message: `"Find school contacts in {city}, {state}."`
3. Streams events from the agent via `runner.run_async()`
4. Logs tool calls in real-time (search queries, scraped URLs)
5. Collects all text output from the agent
6. Parses the collected text as JSON into `SchoolContact` objects

#### 4. Anti-Hallucination Design (Dual-Tool Strategy)

The critical design insight: LLMs will fabricate email addresses if asked to "find emails." This agent avoids that by:

1. **Google Search Sub-Agent** discovers official school URLs
2. **`load_web_page`** scrapes the actual staff directory page
3. The LLM extracts emails from **real HTML content**, not from its training data
4. The system prompt explicitly instructs: "Do NOT guess or hallucinate email addresses"

#### 5. Data Validation (`parse_agent_response()`)

The JSON parser is robust against varied agent output formats:

1. Extracts JSON from markdown code fences (`` ```json ... ``` ``)
2. Falls back to finding raw `{...}` or `[...]` in the text
3. Handles both `{"contacts": [...]}` and bare `[...]` structures
4. Validates each contact through the Pydantic `SchoolContact` schema
5. Skips malformed entries gracefully with warnings

#### 6. Retry Logic (`search_city()`)

Wraps each agent run with exponential backoff for rate limits:

- Catches `429` / `RESOURCE_EXHAUSTED` errors
- Up to 5 retries with delays: 15s, 30s, 60s, 120s, 240s
- Non-rate-limit errors are raised immediately

### Data Flow

```
regions.csv → read_regions() → main() loop
  → asyncio.gather(students_agent, volunteers_agent)
    → search_city() [with retry]
      → _run_agent_once()
        → GoogleSearchAgentTool (find schools)
        → load_web_page (scrape staff pages)
        → LLM produces JSON
      → parse_agent_response() → [SchoolContact, ...]
    → contact_to_row()
  → CsvRepository.append_rows() → students.csv / volunteers.csv
```

### Data Models (`outreach/models.py`)

```python
SchoolContact:
  school_name: str       # Required — full school name
  school_link: str       # URL to school website (default: "")
  faculty_name: str      # Required — contact's full name
  email: str             # Verified email address (default: "")
  dear_line: str         # Salutation, e.g. "Dear Mr. Smith" (default: "")
  comments: str          # Job title or notes (default: "")
```

### Configuration

All tunable parameters live in `outreach/config.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MODEL_ID` | `gemini-3-flash-preview` | Primary LLM for reasoning |
| `MIN_SCHOOLS_TARGET` | `3` | Minimum unique schools per city to consider complete |
| `MIN_CONTACTS_TARGET` | `20` | Minimum total contacts per city to consider complete |
| `STUDENTS_TARGET` | `20` | Contacts per city (elementary/middle) |
| `VOLUNTEERS_TARGET` | `20` | Contacts per city (high school) |
| `MAX_CONCURRENT_AGENTS` | `15` | Concurrent agent limit (semaphore) |
| `AGENT_TIMEOUT` | `300` | Max seconds for a single agent run |
| `MAX_RETRIES` | `5` | Max retry attempts on rate limit |
| `RETRY_BASE_DELAY` | `15.0` | Base backoff delay in seconds |

### Concurrency Model

- **Per-city**: Students and Volunteers agents run in parallel via `asyncio.gather()`
- **Across cities**: Multiple cities are processed **concurrently** (up to `MAX_CONCURRENT_AGENTS`)
- **Sessions**: Each agent run creates a fresh `InMemorySession`
