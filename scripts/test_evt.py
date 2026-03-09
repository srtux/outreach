import asyncio
import sys
from google.genai import types
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from dotenv import load_dotenv

load_dotenv()

async def main():
    agent = LlmAgent(
        name="test",
        model="gemini-3-flash-preview",
        instruction="Find 1 elementary school in Austin, TX. Use google search. Return strictly JSON: {\"contacts\": [{\"school_name\": \"...\", \"school_link\": \"\", \"faculty_name\": \"\", \"email\": \"\", \"dear_line\": \"\", \"comments\": \"\"}]}",
        tools=[google_search],
    )
    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name="test", session_service=session_service)
    session = await session_service.create_session(app_name="test", user_id="user")
    user_msg = types.Content(role="user", parts=[types.Part(text="Austin, TX")])
    
    collected_text = ""
    async for event in runner.run_async(user_id="user", session_id=session.id, new_message=user_msg):
        print("EVENT TYPE:", type(event).__name__)
        if getattr(event, "content", None):
            print("  ROLE:", event.content.role)
            for part in event.content.parts:
                if getattr(part, "text", None):
                    print("  TEXT:", repr(part.text))
                    collected_text += part.text
                elif getattr(part, "function_call", None):
                    print("  FUNC_CALL:", part.function_call.name)
                    # wait, function_call text might be empty, but part.text could have thought?
                elif getattr(part, "function_response", None):
                    print("  FUNC_RESP:", part.function_response.name)
        else:
            print("  NO CONTENT")

    print("\n--- COLLECTED ---")
    print(repr(collected_text))

if __name__ == "__main__":
    asyncio.run(main())
