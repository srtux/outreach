import asyncio
from google.adk.agents import LlmAgent
from google.adk.tools.google_search_agent_tool import create_google_search_agent, GoogleSearchAgentTool
from google.adk.tools.load_web_page import load_web_page as sync_load_web_page

async def load_web_page(url: str) -> str:
    """Fetches the content in the url and returns the text in it.

    Args:
        url (str): The url to browse.

    Returns:
        str: The text content of the url.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(sync_load_web_page, url),
            timeout=15.0
        )
    except Exception as e:
        return f"Error: Failed to load {url}. {e.__class__.__name__}"

from outreach.models import SchoolSearchResult
from outreach.prompts import STUDENTS_SYSTEM_PROMPT, VOLUNTEERS_SYSTEM_PROMPT
from outreach.config import STUDENTS_TARGET, VOLUNTEERS_TARGET, MODEL_ID

def build_agent(agent_type: str) -> LlmAgent:
    """
    Create and configure an LlmAgent for specified research tasks.

    Args:
        agent_type: Either 'students' or 'volunteers' to determine the instructions.

    Returns:
        A configured LlmAgent instance.
    """
    if agent_type == "students":
        instruction = STUDENTS_SYSTEM_PROMPT.format(target=STUDENTS_TARGET)
        name = "students_researcher"
    else:
        instruction = VOLUNTEERS_SYSTEM_PROMPT.format(target=VOLUNTEERS_TARGET)
        name = "volunteers_researcher"

    # search_agent is a lightweight agent specifically for finding search terms and processing SERP results
    search_agent = create_google_search_agent("gemini-2.0-flash")
    search_agent_tool = GoogleSearchAgentTool(agent=search_agent)

    return LlmAgent(
        name=name,
        model=MODEL_ID,
        instruction=instruction,
        tools=[search_agent_tool, load_web_page],
        output_schema=SchoolSearchResult,
    )
