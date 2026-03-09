import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from google.genai.errors import APIError

class MockAPIError(APIError):
    def __init__(self, msg, code=429):
        Exception.__init__(self, msg)
        self.code = code

from outreach.search import parse_agent_response, search_city, _run_agent_once
from outreach.config import MAX_RETRIES
from outreach.models import SchoolContact
from google.genai import types

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

@pytest.mark.asyncio
async def test_search_city_success():
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = [contact]
        result = await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        assert len(result) == 1
        assert result[0] == contact
        mock_run.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_city_timeout_retry():
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # First call timeouts, second succeeds
        mock_run.side_effect = [asyncio.TimeoutError, [contact]]
        
        result = await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        assert len(result) == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_city_rate_limit_retry():
    runner = MagicMock()
    session_service = MagicMock()
    contact = SchoolContact(school_name="S1", faculty_name="F1")
    
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # First call 429, second succeeds
        mock_run.side_effect = [MockAPIError("429 Too Many Requests"), [contact]]
        
        result = await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        assert len(result) == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_awaited_once()

@pytest.mark.asyncio
async def test_search_city_max_retries_fail():
    runner = MagicMock()
    session_service = MagicMock()
    
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # All calls fail with 429
        mock_run.side_effect = MockAPIError("429 Too Many Requests")
        
        with pytest.raises(MockAPIError, match="429 Too Many Requests"):
            await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        
        assert mock_run.call_count == MAX_RETRIES
        assert mock_sleep.call_count == MAX_RETRIES - 1

@pytest.mark.asyncio
async def test_search_city_timeout_all_fail():
    runner = MagicMock()
    session_service = MagicMock()
    
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # All calls timeout
        mock_run.side_effect = asyncio.TimeoutError
        
        result = await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        assert result == []
        assert mock_run.call_count == MAX_RETRIES
        assert mock_sleep.call_count == MAX_RETRIES - 1

@pytest.mark.asyncio
async def test_search_city_other_error():
    runner = MagicMock()
    session_service = MagicMock()
    
    with patch("outreach.search._run_agent_once", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = Exception("Unexpected error")
        
        with pytest.raises(Exception, match="Unexpected error"):
            await search_city(runner, "Austin", "TX", session_service, MagicMock(), MagicMock())
        
        assert mock_run.call_count == 1

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
    contacts = await _run_agent_once(runner, "Austin", "TX", session_service, AsyncMock(), existing_counts)
    
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
    
    contacts = await _run_agent_once(runner, "Austin", "TX", session_service, MagicMock())
    assert len(contacts) == 0
