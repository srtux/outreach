# School Outreach Research Agent

Automated school faculty contact finder powered by [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) with Gemini 3.0 Flash and Google Search grounding.

## Overview

This project reads a **Regions CSV** containing cities and dispatches two AI agents to research school faculty contacts for each city:

| Agent | Target Schools | Target Roles | Contacts per City |
|-------|---------------|-------------|-------------------|
| **Students** | Elementary & Middle Schools | Principal, Vice-Principal, STEM Coordinator, Technology Teacher | 10 |
| **Volunteers** | High Schools | CS Teacher, Robotics Coach, Technology Instructor, CTE Coordinator | 12 |

Each agent uses Google Search to find real school websites and faculty directories, then returns structured contact data.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        main.py                                │
│                                                               │
│  1. Read Regions CSV                                          │
│  2. For each city:                                            │
│     ┌─────────────────────┐   ┌──────────────────────────┐   │
│     │  Students Agent      │   │  Volunteers Agent         │   │
│     │  (LlmAgent)          │   │  (LlmAgent)               │   │
│     │  gemini-3-flash-preview    │   │  gemini-3-flash-preview          │   │
│     │  + GoogleSearchTool  │   │  + GoogleSearchTool        │   │
│     └──────────┬──────────┘   └──────────────┬───────────┘   │
│                │                              │               │
│                ▼                              ▼               │
│         JSON response                  JSON response          │
│         (SchoolContact[])              (SchoolContact[])      │
│                │                              │               │
│                ▼                              ▼               │
│  3. Parse & validate via Pydantic                             │
│  4. Write to output CSVs                                      │
└──────────────────────────────────────────────────────────────┘
```

### Key Components

- **`src/main.py`** — Entry point. Reads the Regions CSV, creates and runs both agents for each city, parses responses, and writes output CSVs.
- **`src/models.py`** — Pydantic data models (`SchoolContact`, `SchoolSearchResult`) that define the expected JSON schema the agents must return.
- **`data/`** — Input and output CSV files.

### How the Agents Work

Each agent is a `google.adk.agents.LlmAgent` configured with:
- **Model**: `gemini-3-flash-preview-preview`
- **Tool**: `google_search` — gives the LLM the ability to perform live Google searches during its reasoning
- **System instruction**: A detailed prompt telling the agent what type of schools and roles to search for, and requiring it to return strictly valid JSON matching the `SchoolContact` schema

When the agent receives a city (e.g., "Find school contacts in Austin, TX"), it:
1. Searches Google for top schools in that city
2. Visits school/district websites to find faculty directories
3. Extracts names, emails, and job titles
4. Returns a JSON object with all contacts

### Data Model

```python
class SchoolContact(BaseModel):
    school_name: str      # "Westlake Elementary School"
    school_link: str      # "https://www.westlake-elem.edu"
    faculty_name: str     # "Dr. Jane Smith"
    email: str            # "jsmith@westlake-elem.edu"
    dear_line: str        # "Dear Dr. Smith"
    comments: str         # "Principal"
```

### Error Handling & Rate Limiting

- **Rate limiter**: A configurable delay (`RATE_LIMIT_DELAY`, default 5 seconds) between each agent call to avoid hitting API quotas.
- **JSON parse fallback**: If the model wraps its response in markdown code fences (`` ```json ... ``` ``), the parser strips them before decoding.
- **Malformed contact skip**: Individual contacts that fail Pydantic validation are skipped with a warning rather than crashing the entire run.
- **City deduplication**: If the same city appears multiple times, it is only searched once.

## Project Structure

```
outreach/
├── data/
│   ├── 2026 Camp Outreach Doc - Regions.csv   # Input (you provide this)
│   ├── outreach_results_students.csv          # Output (generated)
│   └── outreach_results_volunteers.csv        # Output (generated)
├── src/
│   ├── __init__.py
│   ├── main.py          # Agent orchestration, CSV I/O, CLI entry point
│   └── models.py        # Pydantic data models (SchoolContact)
├── .env.example         # Template for your API key
├── .gitignore
├── pyproject.toml       # Python project config & dependencies
└── README.md
```

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- **Google API Key** with access to the Gemini API

## Setup

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Get a Google API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key with Gemini API access
3. Copy the key

### 3. Set the API Key

```bash
export GOOGLE_API_KEY="your-api-key-here"
```

Or copy the example env file and fill it in:

```bash
cp .env.example .env
# Edit .env and paste your key
```

### 4. Prepare Your Regions CSV

Place your file at `data/2026 Camp Outreach Doc - Regions.csv` with these columns:

| City | State |
|------|-------|
| Austin | TX |
| Dallas | TX |
| Houston | TX |

## Usage

```bash
uv run src/main.py
```

uv automatically creates a virtual environment, installs dependencies from `pyproject.toml`, and runs the script.

### Example Output

```
Loaded 3 region entries from 2026 Camp Outreach Doc - Regions.csv

============================================================
Researching: Austin, TX
============================================================
  Searching for 10 elementary/middle contacts...
  Found 10 student contacts.
  Searching for 12 high school CS contacts...
  Found 12 volunteer contacts.

============================================================
Writing output files...
============================================================
Wrote 30 rows to outreach_results_students.csv
Wrote 36 rows to outreach_results_volunteers.csv

Done!
```

### Output CSV Format

Both output files share the same column structure:

| City/State | School Name | School Link | Faculty Name | Email | Dear Line | Comments |
|-----------|-------------|-------------|--------------|-------|-----------|----------|
| Austin, TX | Westlake Elementary | https://... | Dr. Jane Smith | jsmith@... | Dear Dr. Smith | Principal |

## Configuration

Edit the constants at the top of `src/main.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `MODEL_ID` | `gemini-3-flash-preview-preview` | Gemini model identifier |
| `STUDENTS_TARGET` | `10` | Number of elementary/middle school contacts to find per city |
| `VOLUNTEERS_TARGET` | `12` | Number of high school CS contacts to find per city |
| `RATE_LIMIT_DELAY` | `5.0` | Seconds to wait between agent calls |

## Dependencies

Managed via `pyproject.toml`:

| Package | Purpose |
|---------|---------|
| `google-adk` | Google Agent Development Kit — provides `LlmAgent`, `Runner`, `GoogleSearchTool` |
| `pydantic` | Data validation and JSON schema enforcement for agent responses |
| `python-dotenv` | Loads environment variables from `.env` file |

## Notes

- The agent uses **Google Search grounding** — results depend on what is publicly available online. Not all schools publish faculty emails on their websites.
- If an email cannot be found, the field will be an empty string in the output CSV.
- The `google_search` tool is provided by ADK and gives the Gemini model the ability to issue real Google searches as part of its reasoning chain (similar to tool use / function calling).
- Duplicate cities are only searched once to avoid redundant API calls.
