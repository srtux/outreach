import pytest
from google.adk.agents import LlmAgent
from outreach.agents import build_agent

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
