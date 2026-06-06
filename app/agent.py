"""
ADK Agent: pantry chef that generates near-expiry-first meal plans.

Each POST /plan call creates a fresh runner invocation (request-scoped,
no session memory). The agent uses FunctionTools that write/read through
the MongoDB MCP server.
"""
import json
import logging
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

log = logging.getLogger("pantrpilot.agent")

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

CRITICAL OUTPUT RULES:
- After you finish all tool calls (read_pantry, save_meal_plan,
  record_waste_saved), you MUST send one final assistant message whose entire
  text content is a single JSON object matching the schema in step 7.
- The final text message is REQUIRED — do not end the turn with only tool
  calls. The JSON object is the user-visible result.
- No prose, no markdown code fences, no preamble like "Here is your plan:".
- If the pantry is empty, skip save_meal_plan and record_waste_saved and
  return the JSON with plan=[], missing_ingredients=[], waste_saved_grams=0,
  and summary explaining that the pantry is empty.
- Never apologise or chat. Respond with JSON only.
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
    all_text = ""  # fallback: accumulate any text the agent emitted
    event_count = 0
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_message,
    ):
        event_count += 1
        # Collect text from every event with text parts (final or intermediate)
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    all_text += part.text
                    if event.is_final_response():
                        final_text += part.text
    log.info("Agent run: %d events, final_text=%d chars, all_text=%d chars",
             event_count, len(final_text), len(all_text))
    # If the agent never emitted a final text response, fall back to anything
    # textual it said along the way.
    if not final_text.strip():
        final_text = all_text

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
        log.warning("Agent returned non-JSON. Raw text: %r", final_text)
        # Graceful fallback: return a valid plan shape so the UI can display
        # the agent's message instead of a 500.
        message = final_text.strip() or "Agent produced no output."
        return {
            "days": days,
            "plan": [],
            "missing_ingredients": [],
            "waste_saved_grams": 0,
            "summary": message[:400],
        }
