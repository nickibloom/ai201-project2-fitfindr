# FitFindr 🛍️

FitFindr is a tool-using agent that helps you shop secondhand. You describe what
you're looking for in plain language; the agent finds a matching listing, styles
it against your existing wardrobe, and writes a shareable "fit card" caption for
the find. It runs as a Gradio web app and as a command-line script.

---

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (get a free key at [console.groq.com](https://console.groq.com)):

```
GROQ_API_KEY=your_key_here
```

## Running it

```bash
python app.py        # launch the Gradio UI (opens on http://localhost:7860)
python agent.py      # run the CLI happy-path + no-results demo
python utils/data_loader.py   # sanity-check that the data loads
```

## Project layout

```
ai201-project2-fitfindr/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # wardrobe format + example/empty wardrobes
├── utils/
│   └── data_loader.py         # helpers for loading listings & wardrobes
├── tools.py                   # the three agent tools
├── agent.py                   # query parser + planning loop (run_agent)
├── app.py                     # Gradio UI + query handler
├── planning.md                # design spec (tools, loop, state, architecture)
└── requirements.txt
```

---

## Tool Inventory

The agent uses three tools, all defined in [`tools.py`](tools.py).

### 1. `search_listings`

| | |
|---|---|
| **Purpose** | Find secondhand listings that match the user's keywords, with optional size and price filters. Pure Python — no LLM, so it's deterministic and free. |
| **Inputs** | `description: str` — keywords describing the wanted item (e.g. `"vintage graphic tee"`).<br>`size: str \| None` — size to filter by (e.g. `"M"`); `None` skips size filtering.<br>`max_price: float \| None` — inclusive price ceiling; `None` skips price filtering. |
| **Output** | `list[dict]` — matching listings sorted by relevance (best first). Each dict has `id`, `title`, `description`, `category`, `style_tags` (list), `size`, `condition`, `price` (float), `colors` (list), `brand`, `platform`. Empty list if nothing matches. |

**How matching works:** listings are first filtered by `max_price` and `size`, then
each survivor is scored by keyword overlap across its title, description, style
tags, category, colors, and brand. Style-tag/category hits are weighted ×2 so the
most on-style items rank highest; listings that match no keyword are dropped.

Size matching is **token-based**, not substring-based: the listing size is split
on spaces/slashes/parens so `"M"` matches `"S/M"` and `"XL"` matches
`"XL (oversized)"`, while a numeric size like `"8"` does **not** spuriously match
`"W28"` (it does match the half size `"8.5"`).

### 2. `suggest_outfit`

| | |
|---|---|
| **Purpose** | Style a found item against the user's wardrobe, suggesting 1–2 complete outfits. Uses the Groq LLM (`llama-3.3-70b-versatile`). |
| **Inputs** | `new_item: dict` — a listing dict (the item being considered).<br>`wardrobe: dict` — a wardrobe dict with an `items` key (a list of wardrobe item dicts: `id`, `name`, `category`, `colors`, `style_tags`, `notes`). May be empty. |
| **Output** | `str` — a non-empty outfit suggestion. With a populated wardrobe it names specific pieces the user already owns; with an empty wardrobe it gives general styling advice for the item on its own. |

### 3. `create_fit_card`

| | |
|---|---|
| **Purpose** | Turn an outfit suggestion into a short, shareable OOTD caption. Uses the Groq LLM at a higher temperature (1.0) so captions vary between finds. |
| **Inputs** | `outfit: str` — the outfit suggestion string from `suggest_outfit`.<br>`new_item: dict` — the listing dict, used to mention the item name, price, and platform. |
| **Output** | `str` — a 2–4 sentence caption that mentions the item name, price, and platform once each, captures the outfit vibe, and reads differently for different inputs. Returns a descriptive error string (not an exception) if `outfit` is empty. |

---

## Planning Loop

The loop lives in `run_agent()` in [`agent.py`](agent.py). It is a **fixed, linear
pipeline** driven by the contents of the session dict rather than by an LLM
choosing tools. There is exactly one branch — an early exit when the search
returns nothing:

1. **Initialize** a fresh `session` with `_new_session(query, wardrobe)`.
2. **Parse** the query into `description`, `size`, `max_price` (regex/string
   rules in `_parse_query`, no LLM) → `session["parsed"]`.
3. **Search**: `search_listings(**parsed)` → `session["search_results"]`.
   - **If empty:** set `session["error"]` to a helpful message and `return`
     immediately. The LLM tools are never called.
4. **Select** the top-ranked result → `session["selected_item"]`.
5. **Suggest** an outfit: `suggest_outfit(selected_item, wardrobe)` →
   `session["outfit_suggestion"]`.
6. **Create** the fit card: `create_fit_card(outfit_suggestion, selected_item)` →
   `session["fit_card"]`.
7. **Return** the session.

The agent is "done" when it has produced a `fit_card` (success) or set an `error`
(early exit). Because each step reads the output the previous step stored, the
order of tool calls is determined by which session fields are already populated.

**Query parsing** uses regex rather than an LLM call: it pulls `max_price` from
phrases like `"under $30"` or a bare `"$5"`, pulls `size` from an explicit
`"size X"` phrase (or a standalone letter-size token like `"in M"`), and strips
those phrases out of the description so they don't pollute keyword scoring. This
keeps parsing deterministic, instant, and free.

---

## State Management

A single **`session` dict** (created by `_new_session`) is the one source of truth
for an interaction. Every tool reads its inputs from the session and writes its
outputs back into it — nothing is passed through globals or hidden state, so the
full run is inspectable at the end.

| Session field | Written by | Read by |
|---|---|---|
| `query` | `_new_session` (raw user input) | parse step |
| `parsed` (`description`, `size`, `max_price`) | parse step | `search_listings` |
| `search_results` | `search_listings` | selection step |
| `selected_item` | selection step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `_new_session` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | any step that exits early | UI / caller |

`run_agent` returns the completed session, and `handle_query` in [`app.py`](app.py)
reads `error`, `selected_item`, `outfit_suggestion`, and `fit_card` from it to
populate the three UI panels.

---

## Error Handling

Each tool owns a specific failure mode and degrades gracefully — no tool raises an
exception to the caller for an expected failure. All three modes were triggered
deliberately during testing (Milestone 5); concrete outputs are shown below.

| Tool | Failure mode | Agent response |
|---|---|---|
| `search_listings` | No listings match the query | Returns `[]`. The loop sets `session["error"]` and exits before the LLM tools — the user is told what failed and what to try, not just "no results." |
| `suggest_outfit` | Wardrobe is empty | Detects `wardrobe["items"] == []` and returns general styling advice instead of named-piece outfits — never empty, never an exception. |
| `create_fit_card` | Outfit string is missing/empty | Guards against an empty/whitespace `outfit` and returns a descriptive error-message string. |

### Concrete example from testing

**Impossible query → graceful recovery.** Running `search_listings` directly with
an impossible query returns an empty list rather than crashing:

```bash
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]
```

Running the full agent with the same query confirms the loop converts that empty
list into an actionable message and never reaches the LLM tools:

```
error: 'No listings matched "designer ballgown size XXS under $5". Try loosening the size or raising the price.'
outfit_suggestion: None
fit_card: None
```

**Empty outfit → descriptive string, not a traceback.** Calling `create_fit_card`
with an empty outfit returns:

```
Couldn't write a fit card — no outfit suggestion was provided. Try finding an item and generating an outfit first.
```

A notable bug this testing caught early: an initial substring-based size filter
made `"8"` match `"W28"`, returning black jeans for a "size 8 boots" query. The
fix was the token-based `_size_matches` helper described above.

---

## Spec Reflection

Writing `planning.md` before any code paid off most in two places:

- **The session dict as a contract.** Specifying every state field and *who
  writes/reads it* up front meant the planning loop was almost mechanical to
  implement, each step just had to populate the next field. It also made the
  early-exit path obvious: if `search_results` is empty, nothing downstream has
  the input it needs, so the loop must stop there.
- **Per-tool failure modes.** Deciding in advance that each tool degrades (returns
  `[]`, falls back to general advice, or returns an error string) rather than
  raising meant error handling wasn't bolted on afterward — it was part of each
  tool's signature from the start.

Where the implementation **diverged from the spec**: the original spec said size
matching would be "substring-based." Testing showed that was wrong for numeric
sizes (`"8"` matching `"W28"`), so I switched to **token-based** matching and
updated the spec to match the code.

One thing I'd plan differently next time: the spec treated query parsing as a
trivial sub-step, but the regex rules needed real iteration (price phrases vs.
bare `$N`, explicit `"size X"` vs. standalone tokens, stripping matched phrases
out of the description). I'd give it its own spec section next time.

---

## AI Usage

I used **Claude Code** as the implementation assistant throughout,
driven by the specs in `planning.md`. Two specific instances:

### Instance 1 — Implementing `search_listings`

- **What I gave it:** the Tool 1 section of `planning.md` (parameter names/types,
  return shape, and the "returns `[]` on no match" failure mode) plus the existing
  docstring TODO in `tools.py`.
- **What it produced:** the load → filter (price, size) → keyword-score → drop
  zeros → sort implementation, including the ×2 weighting for style-tag/category
  matches and a `_STOPWORDS` set to keep filler words out of scoring.
- **What I changed/overrode:** its first version filtered size with a plain
  substring check. I tested it against `"black combat boots size 8"` and caught a
  false positive where `"8"` matched `"W28"` and surfaced black jeans. I had it
  replace that with the **token-based `_size_matches` helper** (split on
  spaces/slashes/parens, exact-token match, plus a half-size allowance so `"8"`
  matches `"8.5"`), then updated the `planning.md` size description to match.

### Instance 2 — Implementing the planning loop and query parser

- **What I gave it:** the Planning Loop, State Management, and Architecture
  sections of `planning.md` (including the session-field table and the ASCII
  diagram) plus the `run_agent` TODO in `agent.py`.
- **What it produced:** the linear `run_agent` loop wired to populate each session
  field in order with the no-results early exit, and a regex `_parse_query` that
  extracts `description`/`size`/`max_price`.
- **What I verified/changed:** I ran the parser against all five example queries.
  It left a trailing `"in"` on `"90s track jacket in size M"` → `"90s track jacket
  in"`; I confirmed this was harmless because `"in"` is already a stopword in
  `search_listings`, so I kept the simpler parser rather than adding extra
  cleanup. I also verified the early-exit path returned `error` set with
  `outfit_suggestion`/`fit_card` left `None`, matching the spec.
