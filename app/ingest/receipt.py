"""Receipt OCR via Gemini Vision → list of structured ParsedItem dicts."""
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
    import json

    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    prompt = (
        "This is a grocery receipt or shopping list. "
        "Extract every food/grocery item you can identify. "
        "Normalize the name (e.g. '2% MILK 1GAL' → name='milk', quantity=1, unit='gallon', category='dairy'). "
        "Return valid JSON matching the provided schema."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[image_part, prompt],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SCHEMA,
        ),
    )

    data = json.loads(response.text)
    return [ParsedItem(**item) for item in data.get("items", [])]


async def parse_markdown_text(text: str) -> list[ParsedItem]:
    """Feed scraped webpage markdown to Gemini and extract grocery items."""
    import json

    # Trim to avoid token overflow — keep first 8k chars which covers most order pages
    trimmed = text[:8000]
    prompt = (
        "The following is a grocery order confirmation or shopping page (scraped as markdown). "
        "Extract every food/grocery item purchased. "
        "Normalize names (e.g. 'Organic Whole Milk 1 gal' → name='milk', quantity=1, unit='gallon', category='dairy'). "
        "Ignore non-food items (packaging, delivery fees, etc.). "
        "Return valid JSON matching the provided schema.\n\n"
        f"--- PAGE CONTENT ---\n{trimmed}"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SCHEMA,
        ),
    )

    data = json.loads(response.text)
    return [ParsedItem(**item) for item in data.get("items", [])]
