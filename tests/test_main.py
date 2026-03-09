import pytest
import json
import csv
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from src.main import (
    parse_agent_response,
    contact_to_row,
    read_regions,
    build_agent,
    _read_completed_cities,
    search_city,
    _run_agent_once,
    _process_city,
    append_output_csv,
    STUDENTS_TARGET,
    VOLUNTEERS_TARGET,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
)
from src.models import SchoolContact, SchoolSearchResult
from google.adk.agents import LlmAgent

# Existing tests for parse_agent_response
def test_parse_agent_response_valid_json():
    text = json.dumps({"contacts": [{"school_name": "S1", "faculty_name": "F1"}]})
    results = parse_agent_response(text)
    assert len(results) == 1
    assert results[0].school_name == "S1"

def test_parse_agent_response_with_markdown():
    text = "Here is the data:\n```json\n{\"contacts\": [{\"school_name\": \"S1\", \"faculty_name\": \"F1\"}]}\n```"
    results = parse_agent_response(text)
    assert len(results) == 1
    assert results[0].school_name == "S1"

def test_parse_agent_response_bare_list():
    text = json.dumps([{"school_name": "S1", "faculty_name": "F1"}])
    results = parse_agent_response(text)
    assert len(results) == 1
    assert results[0].school_name == "S1"

def test_parse_agent_response_invalid_json():
    text = "Not a JSON"
    results = parse_agent_response(text)
    assert results == []

def test_parse_agent_response_skips_malformed_contacts():
    # json-repair handles slightly malformed json, but fully bad schema fields are skipped
    text = json.dumps({"contacts": [
        {"school_name": "S1", "faculty_name": "F1"},
        {"bad_field": "missing required"},
    ]})
    results = parse_agent_response(text)
    assert len(results) == 1

def test_contact_to_row():
    contact = SchoolContact(school_name="S1", faculty_name="F1", email="e1")
    row = contact_to_row(contact, "Austin", "TX")
    assert row["City/State"] == "Austin, TX"
    assert row["School Name"] == "S1"
    assert row["Email"] == "e1"

def test_read_regions(tmp_path):
    csv_file = tmp_path / "regions.csv"
    content = "City,State\nAustin,TX\nDallas,TX"
    csv_file.write_text(content)

    regions = read_regions(csv_file)
    assert len(regions) == 2
    assert regions[0]["City"] == "Austin"
    assert regions[1]["State"] == "TX"

def test_read_completed_cities(tmp_path):
    csv_file = tmp_path / "output.csv"
    csv_file.write_text("City/State,School Name,School Link,Faculty Name,Email,Dear Line,Comments\n"
                        "\"Austin, TX\",School A,,John,,, \n"
                        "\"Austin, TX\",School A,,Jill,,, \n"
                        "\"Dallas, TX\",School B,,Jane,,, \n")
    seen = _read_completed_cities(csv_file)
    assert "Austin|TX" in seen
    assert "Dallas|TX" in seen
    assert len(seen) == 2
    assert seen["Austin|TX"]["School A"] == 2
    assert seen["Dallas|TX"]["School B"] == 1

def test_read_completed_cities_missing_file(tmp_path):
    seen = _read_completed_cities(tmp_path / "nonexistent.csv")
    assert len(seen) == 0

def test_build_agent_students():
    agent = build_agent("students")
    assert isinstance(agent, LlmAgent)
    assert agent.name == "students_researcher"
    tool_names = [tool.name if hasattr(tool, "name") else tool.__name__ for tool in agent.tools]
    assert "google_search_agent" in tool_names
    assert "load_web_page" in tool_names

def test_build_agent_volunteers():
    agent = build_agent("volunteers")
    assert isinstance(agent, LlmAgent)
    assert agent.name == "volunteers_researcher"
    tool_names = [tool.name if hasattr(tool, "name") else tool.__name__ for tool in agent.tools]
    assert "google_search_agent" in tool_names
    assert "load_web_page" in tool_names

def test_append_output_csv(tmp_path):
    csv_file = tmp_path / "output.csv"
    rows = [{"City/State": "Austin, TX", "School Name": "S1", "School Link": "", "Faculty Name": "F1", "Email": "", "Dear Line": "", "Comments": ""}]
    
    # Should create file and headers
    append_output_csv(csv_file, rows)
    assert csv_file.exists()
    content = csv_file.read_text()
    assert "City/State,School Name" in content
    assert '"Austin, TX",S1' in content
    
    # Append another row, shouldn't duplicate headers
    append_output_csv(csv_file, rows)
    lines = csv_file.read_text().strip().split('\n')
    assert len(lines) == 3  # header + 2 rows

@pytest.mark.asyncio
async def test_search_city_success():
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("src.main._run_agent_once", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = [contact]
        result = await search_city(runner, "Austin", "TX", session_service)
        assert len(result) == 1
        assert result[0] == contact
        mock_run.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_city_timeout_retry():
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("src.main._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # First call timeouts, second succeeds
        mock_run.side_effect = [asyncio.TimeoutError, [contact]]
        
        result = await search_city(runner, "Austin", "TX", session_service)
        assert len(result) == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_city_rate_limit_retry():
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("src.main._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # First call 429, second succeeds
        mock_run.side_effect = [Exception("429 Too Many Requests"), [contact]]
        
        result = await search_city(runner, "Austin", "TX", session_service)
        assert len(result) == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_city_max_retries_fail():
    runner = MagicMock()
    session_service = MagicMock()
    
    with patch("src.main._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # All calls fail with 429
        mock_run.side_effect = Exception("429 Too Many Requests")
        
        with pytest.raises(Exception, match="429 Too Many Requests"):
            await search_city(runner, "Austin", "TX", session_service)
        
        assert mock_run.call_count == MAX_RETRIES
        assert mock_sleep.call_count == MAX_RETRIES - 1

@pytest.mark.asyncio
async def test_search_city_timeout_all_fail():
    runner = MagicMock()
    session_service = MagicMock()
    
    with patch("src.main._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # All calls timeout
        mock_run.side_effect = asyncio.TimeoutError
        
        result = await search_city(runner, "Austin", "TX", session_service)
        assert result == []
        assert mock_run.call_count == MAX_RETRIES
        assert mock_sleep.call_count == MAX_RETRIES - 1

@pytest.mark.asyncio
async def test_search_city_other_error():
    runner = MagicMock()
    session_service = MagicMock()
    
    with patch("src.main._run_agent_once", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = Exception("Unexpected error")
        
        with pytest.raises(Exception, match="Unexpected error"):
            await search_city(runner, "Austin", "TX", session_service)
        
        assert mock_run.call_count == 1

@pytest.mark.asyncio
async def test_process_city():
    stu_runner = MagicMock()
    vol_runner = MagicMock()
    session_service = MagicMock()
    sem = asyncio.Semaphore(1)
    lock = asyncio.Lock()
    progress = {"done": 0, "total": 1}
    
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("src.main.search_city", new_callable=AsyncMock) as mock_search, \
         patch("src.main.append_output_csv") as mock_append:
        
        mock_search.return_value = [contact]
        
        await _process_city(
            "Austin", "TX", stu_runner, vol_runner, session_service, 
            sem, lock, progress, {}, {}
        )
        
        assert mock_search.call_count == 2
        assert mock_append.call_count == 2
        assert progress["done"] == 1

@pytest.mark.asyncio
async def test_process_city_exception():
    stu_runner = MagicMock()
    vol_runner = MagicMock()
    session_service = MagicMock()
    sem = asyncio.Semaphore(1)
    lock = asyncio.Lock()
    progress = {"done": 0, "total": 1}
    
    with patch("src.main.search_city", new_callable=AsyncMock) as mock_search, \
         patch("src.main.append_output_csv") as mock_append:
        
        # Simulating exceptions in gather
        mock_search.side_effect = Exception("mock error")
        
        await _process_city(
            "Austin", "TX", stu_runner, vol_runner, session_service, 
            sem, lock, progress, {}, {}
        )
        
        # Ensure append output was still called, even if rows are empty 
        assert mock_append.call_count == 2
        assert progress["done"] == 1

class FakeEvent:
    def __init__(self, text="", function_calls=None):
        parts = []
        if text:
            parts.append(types.Part(text=text))
        
        self.content = types.Content(role="model", parts=parts) if parts else None
        self._function_calls = function_calls or []

    def get_function_calls(self):
        return self._function_calls

class FakeFunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args

from google.genai import types

@pytest.mark.asyncio
async def test_run_agent_once():
    runner = MagicMock()
    runner.agent.name = "students_researcher"
    runner.app_name = "test_app"
    
    session_service = AsyncMock()
    session_service.create_session.return_value.id = "sesh_123"
    
    async def mock_run_async(*args, **kwargs):
        # Yield a search tool event
        yield FakeEvent(function_calls=[FakeFunctionCall("google_search", {"query": "test"})])
        # Yield a web load tool event
        yield FakeEvent(function_calls=[FakeFunctionCall("load_web_page", {"url": "http://test.com"})])
        # Yield a generic tool
        yield FakeEvent(function_calls=[FakeFunctionCall("other_tool", {})])
        # Yield a text response
        yield FakeEvent(text='{"contacts": [{"school_name": "Test School", "faculty_name": "John Doe", "email": "test@test.com"}]}')
        
    runner.run_async = mock_run_async
    
    existing_counts = {"Old School": 2}
    contacts = await _run_agent_once(runner, "Austin", "TX", session_service, existing_counts)
    
    assert len(contacts) == 1
    assert contacts[0].school_name == "Test School"

@pytest.mark.asyncio
async def test_run_agent_once_no_contacts():
    runner = MagicMock()
    runner.agent.name = "students_researcher"
    
    session_service = AsyncMock()
    session_service.create_session.return_value.id = "sesh_123"
    
    async def mock_run_async(*args, **kwargs):
        yield FakeEvent(text="No contacts found")
        
    runner.run_async = mock_run_async
    
    contacts = await _run_agent_once(runner, "Austin", "TX", session_service)
    assert len(contacts) == 0

from src.main import main

@pytest.mark.asyncio
async def test_main_no_api_key():
    with patch("pathlib.Path.exists", return_value=True), \
         patch("os.environ.get", return_value=None):
        with pytest.raises(SystemExit):
            await main()

@pytest.mark.asyncio
async def test_main_no_csv():
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(SystemExit):
            await main()

@pytest.mark.asyncio
async def test_main_success():
    with patch("pathlib.Path.exists", return_value=True), \
         patch("os.environ.get", return_value="fake_key"), \
         patch("src.main.read_regions", return_value=[{"City": "Aus", "State": "TX"}, {"City": "", "State": ""}]), \
         patch("src.main.build_agent") as mock_build, \
         patch("src.main._read_completed_cities", return_value={}), \
         patch("src.main._process_city", new_callable=AsyncMock) as mock_process:
        
        await main()
        assert mock_build.call_count == 2
        mock_process.assert_awaited_once() # For Aus, TX. The empty city is skipped.

@pytest.mark.asyncio
async def test_main_skip_fully_completed():
    with patch("pathlib.Path.exists", return_value=True), \
         patch("os.environ.get", return_value="fake_key"), \
         patch("src.main.read_regions", return_value=[{"City": "Aus", "State": "TX"}]), \
         patch("src.main.build_agent"), \
         patch("src.main._process_city", new_callable=AsyncMock) as mock_process, \
         patch("src.main._read_completed_cities", side_effect=[
            {"Aus|TX": {"Sch1": 20, "Sch2": 20}}, # students (len >= targets)
            {"Aus|TX": {"Sch3": 20, "Sch4": 20}}, # volunteers
         ]), \
         patch("src.main.STUDENTS_TARGET", 2), \
         patch("src.main.VOLUNTEERS_TARGET", 2):
        
        await main()
        # Should be skipped because targets are met
        assert mock_process.call_count == 0

