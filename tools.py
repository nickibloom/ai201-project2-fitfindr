"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# Common words that carry no search signal — stripped before keyword scoring so
# a query like "looking for a vintage tee" scores on "vintage"/"tee", not "for"/"a".
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "in", "of", "to", "my",
    "im", "i", "looking", "want", "need", "some", "something", "size", "under",
}


# ── Groq client ───────────────────────────────────────────────────────────────

_GROQ_MODEL = "llama-3.3-70b-versatile"


def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(system: str, user: str, temperature: float = 0.7) -> str:
    """
    Send a single system+user prompt to the Groq chat model and return the
    response text. Shared by suggest_outfit and create_fit_card.
    """
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=_GROQ_MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content.strip()


def _describe_item(item: dict) -> str:
    """Compact one-line description of a listing for use inside a prompt."""
    parts = [item["title"]]
    if item.get("brand"):
        parts.append(f"by {item['brand']}")
    parts.append(f"({item['category']}, size {item['size']}, {item['condition']} condition)")
    parts.append(f"— {', '.join(item['colors'])}; tags: {', '.join(item['style_tags'])}")
    return " ".join(parts)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def _size_matches(wanted: str, listing_size: str) -> bool:
    """
    Token-based size match. Splits the listing size on spaces, slashes, and
    parens so "M" matches "S/M" and "XL" matches "XL (oversized)", while a
    numeric size like "8" does NOT spuriously match "W28". A numeric size also
    matches its half size ("8" matches "8.5") since those run small/large enough
    to be worth surfacing.
    """
    wanted = wanted.strip().lower()
    tokens = [t for t in re.split(r"[\s/()]+", listing_size.lower()) if t]
    if wanted in tokens:
        return True
    # Half-size leniency for numeric sizes: "8" matches "8.5" but not "28".
    if wanted.isdigit():
        return any(t.startswith(wanted + ".") for t in tokens)
    return False



def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()

    # 1. Filter by hard constraints first (price, then size).
    candidates = []
    for item in listings:
        if max_price is not None and item["price"] > max_price:
            continue
        if size is not None and not _size_matches(size, item["size"]):
            continue
        candidates.append(item)

    # 2. Tokenize the description into lowercase keywords.
    keywords = [w for w in re.findall(r"[a-z0-9]+", description.lower()) if w not in _STOPWORDS]

    # 3. Score each candidate by keyword overlap across its searchable text.
    scored = []
    for item in candidates:
        haystack = " ".join([
            item["title"],
            item["description"],
            " ".join(item["style_tags"]),
            item["category"],
        ] + item["colors"] + ([item["brand"]] if item.get("brand") else [])).lower()

        score = 0
        for kw in keywords:
            # style_tags / category matches are the strongest signal — weight them.
            if kw in [t.lower() for t in item["style_tags"]] or kw == item["category"].lower():
                score += 2
            elif kw in haystack:
                score += 1

        # 4. Drop anything with no keyword relevance.
        if score > 0:
            scored.append((score, item))

    # 5. Sort by score (highest first) and return just the listing dicts.
    #    Ties keep their original dataset order (Python sort is stable).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_desc = _describe_item(new_item)
    items = wardrobe.get("items", [])

    if not items:
        # Empty wardrobe → general styling advice for the item on its own.
        system = (
            "You are a friendly secondhand-fashion stylist. The shopper hasn't "
            "entered their wardrobe yet, so give general styling ideas for a "
            "single thrifted piece. Suggest what kinds of items pair well with it, "
            "the vibe/occasions it suits, and a couple of concrete outfit ideas. "
            "Keep it to a short, encouraging paragraph or two."
        )
        user = f"The thrifted piece is: {item_desc}\n\nHow should I style it?"
        return _chat(system, user, temperature=0.7)

    # Non-empty wardrobe → name specific pieces the shopper already owns.
    wardrobe_lines = []
    for w in items:
        line = f"- {w['name']} ({w['category']}; {', '.join(w.get('colors', []))}; {', '.join(w.get('style_tags', []))})"
        if w.get("notes"):
            line += f" — {w['notes']}"
        wardrobe_lines.append(line)
    wardrobe_text = "\n".join(wardrobe_lines)

    system = (
        "You are a friendly secondhand-fashion stylist. Suggest 1–2 complete "
        "outfits that pair a newly found thrifted item with pieces the shopper "
        "ALREADY owns. Refer to the owned pieces by their names exactly as listed. "
        "Only use items from the wardrobe list plus the new piece — do not invent "
        "items the shopper doesn't have. For each outfit, give it a short vibe "
        "label and explain in a sentence or two why the pieces work together."
    )
    user = (
        f"New thrifted piece:\n{item_desc}\n\n"
        f"My wardrobe:\n{wardrobe_text}\n\n"
        "Suggest 1–2 outfits using the new piece and my wardrobe."
    )
    return _chat(system, user, temperature=0.7)


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # 1. Guard against missing/empty outfit input.
    if not outfit or not outfit.strip():
        return (
            "Couldn't write a fit card — no outfit suggestion was provided. "
            "Try finding an item and generating an outfit first."
        )

    name = new_item.get("title", "this piece")
    price = new_item.get("price")
    price_str = f"${price:.0f}" if isinstance(price, (int, float)) else "a steal"
    platform = new_item.get("platform", "secondhand")

    system = (
        "You write short, shareable OOTD captions for thrifted fashion finds — "
        "the kind of authentic, slightly playful caption a real person posts on "
        "Instagram or TikTok. Write 2–4 sentences. Mention the item name, its "
        "price, and the platform it's from naturally, each exactly once. Capture "
        "the specific vibe of the outfit. Sound human, not like a product listing. "
        "Light emoji are fine. Return only the caption text."
    )
    user = (
        f"Item: {name}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform}\n\n"
        f"The outfit it's styled in:\n{outfit}\n\n"
        "Write the fit card caption."
    )
    # Higher temperature so captions vary across different finds.
    return _chat(system, user, temperature=1.0)
