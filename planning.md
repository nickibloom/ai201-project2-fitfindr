# FitFindr — planning.md

---

## Tools

### Tool 1: search_listings

**What it does:**
Searches the dataset of thrifted clothing listing to find items that match specified criteria like style, size, keywords, and budget. It filters the available listings to return a specified list of candidate items for the agent to evaluate. 

**Input parameters:**
- `description` (str): Keywords describing what the user wants, e.g. `"vintage graphic tee"`. Scored against each listing's title, description, and style_tags.
- `size` (str | None): Size string to filter by (e.g. `"M"`); `None` skips size filtering. Matching is case-insensitive and token-based — the listing size is split on spaces/slashes/parens so `"M"` matches `"S/M"` and `"XL"` matches `"XL (oversized)"`, while a numeric size like `"8"` does not spuriously match `"W28"` (it does match the half size `"8.5"`).
- `max_price` (float | None): Inclusive price ceiling; `None` skips price filtering.

**What it returns:**
A `list[dict]` of matching listings, sorted by relevance score (best first). Each dict has: `id`, `title`, `description`, `category`, `style_tags` (list), `size`, `condition`, `price` (float), `colors` (list), `brand`, `platform`. Returns an empty list when nothing matches.

**What happens if it fails or returns nothing:**
Returns `[]`. The planning loop detects the empty list, sets `session["error"]` to a friendly "no results" message, and returns early without calling the downstream tools, so the agent never tries to style a non-existent item.

---

### Tool 2: suggest_outfit

**What it does:**
Given a found listing and the user's wardrobe, asks the LLM (Groq) to suggest 1–2 complete outfits that pair the new item with named pieces the user already owns.

**Input parameters:**
- `new_item` (dict): The selected listing dict (the item the user is considering buying).
- `wardrobe` (dict): A wardrobe dict with an `items` key, a list of wardrobe item dicts (`id`, `name`, `category`, `colors`, `style_tags`, `notes`). May be empty.

**What it returns:**
A non-empty string of outfit suggestions. When the wardrobe has items, it names specific pieces from the wardrobe and explains how they combine with the new item. When the wardrobe is empty, it returns general styling advice for the item instead.

**What happens if it fails or returns nothing:**
If `wardrobe["items"]` is empty, it falls back to general styling advice (what pairs well, what vibe it suits) rather than failing. It always returns a usable string so `create_fit_card` downstream has something to work with.

---

### Tool 3: create_fit_card

**What it does:**
Turns an outfit suggestion into a short, shareable caption.

**Input parameters:**
- `outfit` (str): The outfit suggestion string returned by `suggest_outfit`.
- `new_item` (dict): The selected listing dict, used to mention the item name, price, and platform naturally.

**What it returns:**
A 2–4 sentence caption string. It stays casual and authentic, mentions the item name / price / platform once each, captures the outfit vibe in specific terms, and reads differently for different inputs.

**What happens if it fails or returns nothing:**
If `outfit` is empty or whitespace-only, it returns a descriptive error-message string (rather than raising) so the UI can still display something meaningful.

---

### Additional Tools (if any)


---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop in `run_agent()` is a fixed, linear pipeline driven by the contents of the `session` dict rather than by an LLM picking tools. The only branch is the early exit on no search results:

1. Initialize `session` with `_new_session(query, wardrobe)`.
2. **Parse** the query into `description`, `size`, and `max_price`; store in `session["parsed"]`.
3. Call `search_listings(**parsed)` → `session["search_results"]`.
   - **If empty:** set `session["error"]` to a helpful message and `return` immediately. The loop stops here, `suggest_outfit` and `create_fit_card` are never called.
4. **Select** the top result (`search_results[0]`) → `session["selected_item"]`.
5. Call `suggest_outfit(selected_item, wardrobe)` → `session["outfit_suggestion"]`.
6. Call `create_fit_card(outfit_suggestion, selected_item)` → `session["fit_card"]`.
7. Return `session`.

The agent knows it's "done" when it has produced a `fit_card` (success) or when it has set `session["error"]` (early exit). Each step's input is the previous step's stored output, so the order of tool calls is determined by which session fields are already populated.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session`) is the one source of truth for the whole interaction. Each tool reads its inputs from the session and writes its outputs back into the session:

| Session field | Written by | Read by |
|---|---|---|
| `query` | `_new_session` (the raw user input) | parse step |
| `parsed` (`description`, `size`, `max_price`) | parse step | `search_listings` |
| `search_results` | `search_listings` | selection step |
| `selected_item` | selection step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `_new_session` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | any step that exits early | UI / caller |

Nothing is passed via globals or hidden state, every value flows through the session dict, so the full run is inspectable at the end. `run_agent` returns the completed session, and `handle_query` in `app.py` reads `error`, `selected_item`, `outfit_suggestion`, and `fit_card` from it to populate the three UI panels.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Tool returns `[]`. The loop sets `session["error"]` to a friendly message (e.g. "No listings matched 'designer ballgown size XXS under $5' — try loosening the size or raising the price.") and returns early; downstream tools are skipped. |
| suggest_outfit | Wardrobe is empty | Tool detects `wardrobe["items"] == []` and returns general styling advice for the item instead of named-piece outfits, never an empty string or exception. The loop continues normally. |
| create_fit_card | Outfit input is missing or incomplete | Tool guards against an empty/whitespace `outfit` and returns a descriptive error-message string (no exception), so the UI still has text to show. |

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                  app.py (Gradio UI)           │
                    │   query box + wardrobe choice → handle_query  │
                    └───────────────────────┬──────────────────────┘
                                            │ run_agent(query, wardrobe)
                                            ▼
        ┌───────────────────────────────────────────────────────────────┐
        │                    agent.py — Planning Loop                     │
        │                                                                 │
        │   1. _new_session(query, wardrobe)  ───────────►  ┌──────────┐  │
        │   2. parse query → parsed                         │ session  │  │
        │   3. search_listings(**parsed) ──────────────────►│  dict    │  │
        │         │                                         │ (state)  │  │
        │         ├── [] empty? ──► set error ──► RETURN ───►│          │  │
        │         ▼                                         │ query    │  │
        │   4. selected_item = results[0] ─────────────────►│ parsed   │  │
        │   5. suggest_outfit(item, wardrobe) ─────────────►│ results  │  │
        │   6. create_fit_card(outfit, item) ──────────────►│ item     │  │
        │   7. RETURN session                               │ outfit   │  │
        │                                                   │ fit_card │  │
        │                                                   │ error    │  │
        └───────────────────────────────────────────────── └──────────┘──┘
              │                    │                    │
              ▼                    ▼                    ▼
        ┌───────────┐      ┌──────────────┐     ┌────────────────┐
        │  Tool 1   │      │   Tool 2     │     │    Tool 3      │
        │  search_  │      │  suggest_    │     │  create_fit_   │
        │  listings │      │  outfit      │     │  card          │
        │ (no LLM)  │      │  (Groq LLM)  │     │  (Groq LLM)    │
        └─────┬─────┘      └──────────────┘     └────────────────┘
              │
              ▼
        load_listings()  (utils/data_loader.py → data/listings.json)

   Error paths (dashed): empty search results → session["error"] → early return → UI panel 1.
   Empty wardrobe → suggest_outfit falls back to general advice (no branch out of loop).
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**

I'll use **Claude** to implement the three tools in `tools.py`, one at a time, giving it the matching Tool section above (inputs, return value, failure mode) plus the docstring TODO already in `tools.py`.

- **search_listings:** Give Claude the Tool 1 spec, the listing field list, and `load_listings()` from the data loader. Expect: load → filter by `max_price` and `size` (case-insensitive substring) → score by keyword overlap across title/description/style_tags → drop score-0 → sort desc. **Verify** against 3 queries: `"vintage graphic tee under $30"` (should return tops with vintage/graphic tags), `"black combat boots size 8"` (size filter), and `"designer ballgown size XXS under $5"` (must return `[]`).
- **suggest_outfit:** Give Claude the Tool 2 spec + the wardrobe schema. Expect a function that branches on empty wardrobe and otherwise formats wardrobe items into the prompt. **Verify** by passing the example wardrobe + a found tee (output should name real wardrobe pieces like the baggy jeans / chunky sneakers) and the empty wardrobe (output should be general advice, non-empty).
- **create_fit_card:** Give Claude the Tool 3 spec + style guidelines. Expect a guarded function using higher temperature. **Verify** the empty-outfit guard returns an error string, and that two different outfits produce two different captions that each mention item name/price/platform.

**Milestone 4 — Planning loop and state management:**

I'll give **Claude** the Planning Loop + State Management + Architecture sections above plus the `run_agent` TODO, and ask it to implement the loop exactly as the session-field table describes. For query parsing I'll start with **regex/string parsing** in Python (extract `under $N` → `max_price`, `size M/L/XL/W30...` → `size`, remainder → `description`) for determinism and zero API cost, and only escalate to an LLM parse tool if regex proves too brittle on the example queries. **Verify** by running `python agent.py`, which exercises both the happy path (graphic tee → item found, outfit, fit card all populated, `error is None`) and the no-results path (`designer ballgown` → `error` set, other fields `None`). Then run `python app.py` and confirm the three panels populate correctly, including the deliberate no-results example query.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1:**
`handle_query` (UI) selects the example wardrobe and calls `run_agent(query, wardrobe)`. `run_agent` builds a fresh `session`, then **parses** the query: `description="vintage graphic tee"`, `size=None`, `max_price=30.0`, stored in `session["parsed"]`.

**Step 2:**
The loop calls `search_listings("vintage graphic tee", None, 30.0)`. It loads the 40 listings, drops anything over $30, scores the rest on keyword overlap, and returns matches best-first (e.g. the **Y2K Baby Tee — Butterfly Print, $18, vintage/graphic tee tags**). Results stored in `session["search_results"]`; the top one becomes `session["selected_item"]`.

**Step 3:**
The loop calls `suggest_outfit(selected_item, wardrobe)`. The wardrobe has items, so the LLM is prompted with the tee plus the user's pieces and returns something like: "Pair the butterfly baby tee with your **baggy straight-leg jeans** and **chunky white sneakers**, then throw the **vintage black denim jacket** over it for a y2k-streetwear look." Stored in `session["outfit_suggestion"]`.

**Step 4:**
The loop calls `create_fit_card(outfit_suggestion, selected_item)`. The LLM (higher temperature) returns a 2–4 sentence caption mentioning the item name, $18 price, and Depop platform, capturing the vibe. Stored in `session["fit_card"]`. `run_agent` returns the session.

**Final output to user:**
`handle_query` maps the session to the three panels:
- **Top listing found:** a formatted summary of `selected_item` (title, price, size, condition, platform, description).
- **Outfit idea:** the `outfit_suggestion` text.
- **Your fit card:** the shareable `fit_card` caption.

(For the no-results query "designer ballgown size XXS under $5", Step 2 returns `[]`, `session["error"]` is set, and the user sees the error message in panel 1 with the other two panels empty.)
