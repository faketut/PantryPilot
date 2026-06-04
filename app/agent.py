"""
ADK Agent: pantry chef that generates near-expiry-first meal plans.

Each POST /plan call creates a fresh runner invocation (request-scoped,
no session memory). The agent uses FunctionTools that write/read through
the MongoDB MCP server.
"""
import json
import uuid

from google.adk import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai.types import Content, Part

from app.tools_local import (
    read_pantry,
    save_meal_plan,
    record_waste_saved,
    get_waste_stats,
)

SYSTEM_PROMPT = """
You are PantryPilot, an AI chef that minimises food waste.

Your job for every plan request:
1. Call read_pantry() to get the current pantry sorted by expiry (soonest first).
2. Identify items expiring within 5 days — these are PRIORITY ingredients.
3. Draft a {days}-day meal plan that uses priority ingredients on day 1 and 2.
4. List ingredients required by the plan vs. what is in the pantry → produce a
   'missing_ingredients' list (items not in pantry or with quantity 0).
5. Call save_meal_plan() with the full plan JSON.
6. For each priority item included in the plan, call record_waste_saved() with
   an estimated weight in grams (use 200g for produce, 500g for dairy, 300g for meat,
   100g for condiments/spices, 400g for other).
7. Return a JSON object with keys:
   - "days": int
   - "plan": list of {{"day": int, "meals": [{{"meal": str, "recipe": str, "ingredients": [str]}}]}}
   - "missing_ingredients": list of str
   - "waste_saved_grams": float
   - "summary": str (one-sentence human-readable summary)

Respond ONLY with valid JSON — no markdown, no explanation outside the JSON.
""".strip()


def _make_agent(days: int = 5) -> Agent:
    return Agent(
        name="pantry_chef",
        model="gemini-2.5-flash",
        instruction=SYSTEM_PROMPT.replace("{days}", str(days)),
        tools=[
            FunctionTool(func=read_pantry),
            FunctionTool(func=save_meal_plan),
            FunctionTool(func=record_waste_saved),
            FunctionTool(func=get_waste_stats),
        ],
    )


async def run_plan_agent(days: int = 5) -> dict:
    """Run one planning cycle; returns the structured plan dict."""
    agent = _make_agent(days)
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name="pantrpilot",
        session_service=session_service,
    )

    session_id = str(uuid.uuid4())
    user_id = "demo"
    await session_service.create_session(
        app_name="pantrpilot",
        user_id=user_id,
        session_id=session_id,
    )

    user_message = Content(
        role="user",
        parts=[Part(text=f"Generate a {days}-day meal plan from my pantry.")],
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_message,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    # Strip potential markdown code fences
    cleaned = final_text.strip()
    for prefix in ("```json", "```JSON", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try extracting JSON object from anywhere in the response
        import re
        m = re.search(r'\{[\s\S]+\}', final_text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {"error": "Agent returned non-JSON", "raw": final_text[:500]}
