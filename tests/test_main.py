import pytest
import json
import csv
from pathlib import Path
from src.main import parse_agent_response, contact_to_row, read_regions, build_agent, STUDENTS_TARGET, VOLUNTEERS_TARGET
from src.models import SchoolContact
from google.adk.agents import LlmAgent

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

def test_parse_agent_response_invalid_json():
    text = "Not a JSON"
    results = parse_agent_response(text)
    assert results == []

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

def test_build_agent_students():
    agent = build_agent("students")
    assert isinstance(agent, LlmAgent)
    assert agent.name == "students_researcher"
    tool_names = [tool.name if hasattr(tool, "name") else tool.__name__ for tool in agent.tools]
    assert "google_search_agent" in tool_names
    assert "load_web_page" in tool_names
    assert str(STUDENTS_TARGET) in agent.instruction
    assert "load_web_page" in agent.instruction

def test_build_agent_volunteers():
    agent = build_agent("volunteers")
    assert isinstance(agent, LlmAgent)
    assert agent.name == "volunteers_researcher"
    tool_names = [tool.name if hasattr(tool, "name") else tool.__name__ for tool in agent.tools]
    assert "google_search_agent" in tool_names
    assert "load_web_page" in tool_names
    assert str(VOLUNTEERS_TARGET) in agent.instruction
    assert "load_web_page" in agent.instruction
