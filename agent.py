"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parsing ─────────────────────────────────────────────────────────────

# Letter sizes we recognize as standalone tokens (e.g. "in M") when there's no
# explicit "size ..." phrase. Numeric sizes are only read from "size N".
_SIZE_WORDS = {"xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl"}


def _parse_query(query: str) -> dict:
    """
    Extract a description, size, and max_price from a natural language query
    using regex/string rules (no LLM — deterministic and free).

    Examples:
        "vintage graphic tee under $30"     -> desc="vintage graphic tee", max_price=30.0
        "90s track jacket in size M"        -> desc="90s track jacket", size="M"
        "designer ballgown size XXS under $5" -> desc="designer ballgown", size="XXS", max_price=5.0

    The matched price/size phrases are stripped out of the description so they
    don't pollute keyword scoring in search_listings.
    """
    q = query
    max_price = None
    size = None

    # Price: "under $30" / "below 40" / "less than $5" / a bare "$5".
    m = re.search(
        r"(?:under|below|less than|cheaper than|max|<)\s*\$?\s*(\d+(?:\.\d+)?)",
        q, re.IGNORECASE,
    )
    if m is None:
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", q)
    if m is not None:
        max_price = float(m.group(1))
        q = q[:m.start()] + " " + q[m.end():]

    # Size: explicit "size X" (letters, digits, or things like "S/M").
    ms = re.search(r"\bsize\s+([A-Za-z0-9]+(?:/[A-Za-z0-9]+)?)", q, re.IGNORECASE)
    if ms is not None:
        size = ms.group(1)
        q = q[:ms.start()] + " " + q[ms.end():]
    else:
        # Fallback: a standalone letter-size token like "in M".
        for tok in re.findall(r"[A-Za-z]+", q):
            if tok.lower() in _SIZE_WORDS:
                size = tok
                q = re.sub(rf"\b{re.escape(tok)}\b", " ", q, count=1)
                break

    description = re.sub(r"\s+", " ", q).strip(" ,.")
    return {"description": description, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1: fresh session — the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Step 2: parse the query into search parameters.
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]

    # Step 3: search the listings.
    session["search_results"] = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )
    if not session["search_results"]:
        # No matches — set a helpful error and exit before the LLM tools.
        session["error"] = (
            f"No listings matched “{query}”. "
            "Try loosening the size or raising the price."
        )
        return session

    # Step 4: select the top-ranked result.
    session["selected_item"] = session["search_results"][0]

    # Step 5: suggest an outfit pairing it with the user's wardrobe.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], wardrobe
    )

    # Step 6: turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: return the completed session.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
