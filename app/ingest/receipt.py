"""Receipt OCR via Gemini Vision → list of structured ParsedItem dicts."""
import asyncio
import json

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app.config import GOOGLE_API_KEY

client = genai.Client(api_key=GOOGLE_API_KEY)


class ParsedItem(BaseModel):
    name: str
    quantity: float = 1.0
    unit: str = "unit"
    category: str = "general"


_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "dairy", "produce", "meat", "seafood", "bakery",
                            "frozen", "canned", "beverage", "snack",
                            "condiment", "grain", "spice", "general",
                        ],
                    },
                },
                "required": ["name", "category"],
            },
        }
    },
    "required": ["items"],
}


async def parse_receipt(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[ParsedItem]:
    """Call Gemini Vision with structured output schema; returns normalized items."""
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    prompt = (
        "This is a grocery receipt or shopping list. "
        "Extract every food/grocery item you can identify. "
        "Normalize the name (e.g. '2% MILK 1GAL' → name='milk', quantity=1, unit='gallon', category='dairy'). "
        "Return valid JSON matching the provided schema."
    )

    # google-genai's generate_content is synchronous and blocks the event loop;
    # offload to a worker thread so the FastAPI handler stays responsive.
    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-2.5-flash",
        contents=[image_part, prompt],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SCHEMA,
        ),
    )

    if not response.text:
        return []
    data = json.loads(response.text)
    return [ParsedItem(**item) for item in data.get("items", [])]
