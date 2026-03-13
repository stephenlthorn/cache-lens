# TokenLens — Product Design Spec v1.0

---

# 1. Product Summary

**TokenLens** is a local-first CLI and web tool that analyzes AI prompts, prompt chains, and agent traces to find token waste and generate concrete optimization suggestions.

**For:** Indie builders, home lab operators, and small teams running LLM-powered workflows who are spending more on tokens than they should be.

**Problem it solves:** Most users have no visibility into *why* their token bills are high. They paste the same system prompts repeatedly, structure prompts in cache-hostile order, embed large static blocks alongside small dynamic parts, and have no way to see it. Existing tools count tokens but don't tell you what to fix.

**Why users care:** "Show me exactly where I'm wasting money and tell me how to restructure my prompts to spend less." That's it.

---

# 2. MVP Scope

## Included in V1

- Paste or upload a single prompt, prompt chain, or JSON trace
- Deterministic analysis engine (rules-based, no LLM dependency)
- Repeated block detection across messages in a chain
- Static vs dynamic classification
- Token estimation per section
- Cacheability score (0-100)
- Waste summary with ranked sources
- Concrete restructuring suggestions
- Local web UI (`uvx tokenlens ui`)
- CLI with JSON output (`uvx tokenlens analyze`)
- Zero backend — everything runs locally in-process

## Explicitly excluded from V1

- Real-time streaming/intercept proxy
- Direct API integrations (OpenAI, Anthropic SDK hooks)
- Historical tracking or dashboards
- Multi-user or team features
- Cloud hosting
- LLM-powered suggestion generation
- Trace format auto-detection for arbitrary providers
- Diff/comparison between analysis runs

## Smallest lovable product

Paste a prompt chain → see a score, a waste breakdown, and a restructured version. Under 30 seconds from launch to insight.

## Killer use case

A developer running a multi-turn agent loop pastes their trace JSON, sees that 4,200 tokens of system prompt are repeated identically across 12 calls, and gets a restructured layout that would cut their token usage by 38%.

---

# 3. Target Users

### User 1: The Indie AI Builder (Primary)

- **Who:** Solo developer building AI-powered tools, SaaS products, or automations
- **Trying to do:** Ship fast, keep costs low, iterate on prompt design
- **Frustration:** Token bills creep up; no idea which prompts are wasteful; trial-and-error optimization
- **Why try:** Free, local, instant feedback on prompt efficiency
- **Why stay:** Becomes part of their prompt development workflow — run analysis before deploying prompt changes

### User 2: The Home Lab Operator

- **Who:** Technical enthusiast running local LLMs or API-backed agents at home
- **Trying to do:** Build agent workflows, keep costs under hobby budget
- **Frustration:** Agent loops burn tokens fast; no visibility into per-call breakdown
- **Why try:** Curiosity + cost pressure; tool is local-first and privacy-friendly
- **Why stay:** Ongoing agent workflow optimization

### User 3: The Small Team Lead

- **Who:** Tech lead at a 2-10 person team shipping AI features
- **Trying to do:** Control AI costs without dedicated MLOps
- **Frustration:** No one owns prompt efficiency; developers copy-paste prompts without thinking about cache structure
- **Why try:** Drop-in tool that any developer can use without setup
- **Why stay:** Integrates into CI/CD later (V2); becomes a quality gate

**Priority order:** Indie builder > Home lab > Small team

---

# 4. Inputs Supported

## Input Type 1: Raw Prompt Text

Plain text pasted or uploaded. Treated as a single message.

```
You are a helpful assistant that...
[long system prompt]
```

- **Required fields:** Text content (non-empty string)
- **Optional fields:** None
- **Validation:** Must be non-empty. Warn if under 50 tokens (too short to optimize). No upper limit but warn above 200K tokens.
- **Malformed handling:** If empty, show "Nothing to analyze" with input prompt.

## Input Type 2: Structured Prompt Chain (Messages Array)

A JSON array of messages, matching the OpenAI/Anthropic message format.

```json
{
  "messages": [
    {"role": "system", "content": "You are..."},
    {"role": "user", "content": "Analyze this..."},
    {"role": "assistant", "content": "Here is..."},
    {"role": "user", "content": "Now do..."}
  ]
}
```

- **Required fields:** `messages` array with at least one object. Each message must have `role` (string) and `content` (string).
- **Optional fields:** `model` (string), `temperature`, `max_tokens`, `metadata` (object, ignored but preserved)
- **Validation:** Must be valid JSON. `messages` must be an array. Each message must have `role` and `content`. Unknown roles are accepted but flagged.
- **Malformed handling:** JSON parse errors → show error with line number. Missing `messages` key → attempt to treat top-level array as messages. Missing `role`/`content` → skip message with warning.

## Input Type 3: Multi-Call Trace

A JSON array of API calls, representing a workflow or agent loop.

```json
{
  "calls": [
    {
      "call_id": "optional-id",
      "messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
      ],
      "model": "claude-sonnet-4-6",
      "usage": {
        "input_tokens": 1500,
        "output_tokens": 300,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0
      }
    }
  ]
}
```

- **Required fields:** `calls` array. Each call must have `messages` array.
- **Optional fields:** `call_id`, `model`, `usage` (with any token count subfields), `timestamp`, `metadata`
- **Validation:** Must be valid JSON. `calls` must be non-empty array. Each call must contain valid `messages`.
- **Malformed handling:** Same JSON error handling as above. Calls missing `messages` are skipped with warning. If `usage` is present, validate that token fields are non-negative integers.

## Input Detection Logic

The app should auto-detect input type:

```
if input is not valid JSON:
    treat as raw prompt text
elif input has "calls" key with array value:
    treat as multi-call trace
elif input has "messages" key with array value:
    treat as prompt chain
elif input is a JSON array of objects with "role" + "content":
    treat as prompt chain (bare messages array)
else:
    treat as raw prompt text (stringify the JSON)
```

---

# 5. Analysis Engine

The engine is fully deterministic and rules-based. No LLM calls.

## 5.1 Tokenization

Use `tiktoken` (via `tiktoken` Python package) with `cl100k_base` encoding as default. This covers GPT-4, Claude (close enough for estimation), and most modern models.

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")
def count_tokens(text: str) -> int:
    return len(enc.encode(text))
```

## 5.2 Repeated Block Detection

Find content blocks that appear in multiple messages or calls.

**Algorithm:**

1. Extract all content strings from all messages across all calls.
2. For each content string, split into paragraphs (double newline) and sentences.
3. Build a frequency map of normalized blocks (lowercase, whitespace-collapsed) → list of locations.
4. A block is "repeated" if:
   - It appears in 2+ distinct messages/calls
   - It is ≥ 50 tokens long
5. Rank repeated blocks by `token_count × (occurrence_count - 1)` = tokens wasted.

```python
def find_repeated_blocks(contents: list[str], min_tokens: int = 50) -> list[RepeatedBlock]:
    block_map: dict[str, list[Location]] = {}
    for doc_idx, content in enumerate(contents):
        blocks = split_into_blocks(content)  # paragraphs, then sentences for short content
        for block in blocks:
            normalized = normalize(block)
            if count_tokens(block) >= min_tokens:
                block_map.setdefault(normalized, []).append(Location(doc_idx, block))

    repeated = []
    for norm, locations in block_map.items():
        if len(locations) >= 2:
            token_count = count_tokens(locations[0].original)
            waste = token_count * (len(locations) - 1)
            repeated.append(RepeatedBlock(
                content=locations[0].original,
                occurrences=len(locations),
                tokens_per_occurrence=token_count,
                total_waste=waste,
                locations=locations
            ))
    return sorted(repeated, key=lambda r: r.total_waste, reverse=True)
```

## 5.3 Static vs Dynamic Classification

Classify each block as static (same across calls, cacheable) or dynamic (varies, not cacheable).

**For single prompts:** Use heuristics:
- Blocks containing template variables (`{{var}}`, `{var}`, `$var`, `<var>`) → dynamic
- Blocks containing timestamps, dates, UUIDs, or URLs with query params → dynamic
- Blocks that are instructions, persona definitions, or formatting rules → static
- Blocks containing user-specific data, names, or conversation history → dynamic

**For multi-call traces:** Use diff-based detection:
- Compare content across calls at the same message position (e.g., system message across calls)
- If content is identical across all calls → static
- If content varies → dynamic
- Partially varying content: find the longest common prefix/suffix → static portions; middle → dynamic

```python
def classify_static_dynamic(calls: list[Call]) -> list[Section]:
    sections = []
    # Group messages by position and role across calls
    for position in range(max_messages):
        contents_at_position = [call.messages[position].content for call in calls if len(call.messages) > position]
        if len(set(contents_at_position)) == 1:
            sections.append(Section(content=contents_at_position[0], classification="static", confidence=1.0))
        elif len(set(contents_at_position)) == len(contents_at_position):
            sections.append(Section(content=contents_at_position[0], classification="dynamic", confidence=1.0))
        else:
            # Partial: find common prefix/suffix
            prefix = common_prefix(contents_at_position)
            suffix = common_suffix(contents_at_position)
            if len(prefix) > 0:
                sections.append(Section(content=prefix, classification="static", confidence=0.9))
            sections.append(Section(content="[varies]", classification="dynamic", confidence=0.9))
            if len(suffix) > 0:
                sections.append(Section(content=suffix, classification="static", confidence=0.9))
    return sections
```

**Heuristic patterns for single-prompt mode:**

| Pattern | Classification | Confidence |
|---------|--------------|------------|
| `{{...}}` or `{...}` template syntax | Dynamic | 0.95 |
| ISO timestamps, UUIDs | Dynamic | 0.9 |
| "You are a...", "Your role is..." | Static | 0.85 |
| "Respond in...", "Format as..." | Static | 0.85 |
| JSON/XML example blocks | Static | 0.8 |
| User names, emails, specific entities | Dynamic | 0.7 |

## 5.4 Cacheability Scoring

Score from 0 (uncacheable) to 100 (perfectly structured for caching).

**Scoring formula:**

```python
def cacheability_score(analysis: Analysis) -> int:
    score = 100

    # Penalty: static content not at the beginning (-30 max)
    # Anthropic/OpenAI cache from prefix. Static content should come first.
    static_prefix_ratio = tokens_in_static_prefix / total_static_tokens
    score -= int((1 - static_prefix_ratio) * 30)

    # Penalty: repeated blocks across calls (-25 max)
    repeat_waste_ratio = total_repeated_waste_tokens / total_input_tokens
    score -= int(min(repeat_waste_ratio * 100, 25))

    # Penalty: dynamic content interleaved with static (-20 max)
    interleave_count = count_static_dynamic_transitions(sections)
    score -= int(min(interleave_count * 5, 20))

    # Penalty: no clear static prefix (-15)
    if not has_static_prefix(sections):
        score -= 15

    # Penalty: very short static blocks (<100 tokens) that could be merged (-10 max)
    fragmented_statics = count_short_static_blocks(sections, threshold=100)
    score -= int(min(fragmented_statics * 2, 10))

    return max(0, score)
```

**Score interpretation:**

| Score | Label | Meaning |
|-------|-------|---------|
| 80-100 | Excellent | Well-structured for caching |
| 60-79 | Good | Minor improvements possible |
| 40-59 | Fair | Significant optimization available |
| 20-39 | Poor | Major restructuring needed |
| 0-19 | Critical | Almost no caching benefit possible |

## 5.5 Waste Source Ranking

Each waste source gets a type, token count, and priority.

**Waste types:**

| Type | Detection | Priority Weight |
|------|-----------|----------------|
| `repeated_block` | Same content in multiple calls | 1.0 × waste tokens |
| `misplaced_dynamic` | Dynamic content before static content | 0.8 × affected tokens |
| `interleaved` | Static/dynamic alternating frequently | 0.6 × affected tokens |
| `oversized_context` | Single message > 50% of total tokens | 0.4 × excess tokens |
| `redundant_instructions` | Near-duplicate instruction blocks (>80% similarity) | 0.9 × duplicate tokens |

Rank by `priority_weight` descending. Show top 5 in summary, all in detailed view.

## 5.6 Restructuring Suggestions

Generate concrete suggestions, not vague advice.

**Rules:**

1. **Consolidate repeated blocks:** If block X appears in N calls, suggest extracting to a single system prompt prefix. Show the extracted block.
2. **Reorder for cache efficiency:** If dynamic content appears before static content, suggest moving all static content to the beginning. Show the reordered layout.
3. **Merge fragmented statics:** If multiple small static blocks exist, suggest merging into one contiguous block.
4. **Extract templates:** If dynamic content follows a pattern, suggest a template with placeholders.
5. **Trim redundant instructions:** If near-duplicate instruction blocks exist, suggest consolidating.

Each suggestion includes:
- What to change (specific text reference)
- Why (which waste source it addresses)
- Estimated token savings
- The restructured version (actual text, not description)

---

# 6. Outputs

## 6.1 Cacheability Score

- **Meaning:** How well-structured this prompt/chain/trace is for LLM prefix caching
- **Calculation:** See Section 5.4
- **UI display:** Large number (0-100) with color (red/yellow/green) and label. Circular gauge or bold text.
- **CLI JSON:**
```json
{
  "cacheability_score": 62,
  "cacheability_label": "Good",
  "score_breakdown": {
    "static_prefix_penalty": -10,
    "repetition_penalty": -15,
    "interleave_penalty": -5,
    "no_prefix_penalty": 0,
    "fragmentation_penalty": -8
  }
}
```

## 6.2 Repeated Context Findings

- **Meaning:** Content blocks duplicated across messages or calls
- **Calculation:** See Section 5.2
- **UI display:** List of repeated blocks, each showing: snippet (first 200 chars), occurrence count, tokens per occurrence, total waste. Expandable to see full content and locations.
- **CLI JSON:**
```json
{
  "repeated_blocks": [
    {
      "content_preview": "You are an expert assistant...",
      "content_hash": "sha256:abc123...",
      "occurrences": 5,
      "tokens_per_occurrence": 840,
      "total_waste_tokens": 3360,
      "locations": [
        {"call_index": 0, "message_index": 0},
        {"call_index": 1, "message_index": 0}
      ]
    }
  ]
}
```

## 6.3 Top Waste Sources

- **Meaning:** Ranked list of where tokens are being wasted
- **Calculation:** See Section 5.5
- **UI display:** Ordered list. Each item: waste type icon/badge, description, token count, percentage of total. Top source highlighted.
- **CLI JSON:**
```json
{
  "waste_sources": [
    {
      "type": "repeated_block",
      "description": "System prompt repeated across 5 calls",
      "waste_tokens": 3360,
      "percentage_of_total": 22.4,
      "priority_score": 3360.0
    }
  ],
  "total_waste_tokens": 5120,
  "total_input_tokens": 15000,
  "waste_percentage": 34.1
}
```

## 6.4 Static vs Dynamic Breakdown

- **Meaning:** Which parts of your prompts are fixed vs varying
- **Calculation:** See Section 5.3
- **UI display:** Stacked bar chart showing static (blue) vs dynamic (orange) token distribution. Below: annotated content view with static sections highlighted in blue, dynamic in orange.
- **CLI JSON:**
```json
{
  "static_dynamic_breakdown": {
    "total_static_tokens": 8500,
    "total_dynamic_tokens": 6500,
    "static_percentage": 56.7,
    "sections": [
      {
        "classification": "static",
        "confidence": 1.0,
        "token_count": 840,
        "content_preview": "You are an expert...",
        "position": "prefix"
      }
    ]
  }
}
```

## 6.5 Optimization Suggestions

- **Meaning:** Specific, actionable changes to reduce waste
- **Calculation:** See Section 5.6
- **UI display:** Numbered cards. Each card: title, explanation, before/after diff, estimated savings. Cards ordered by impact.
- **CLI JSON:**
```json
{
  "suggestions": [
    {
      "id": "s1",
      "type": "consolidate_repeated",
      "title": "Extract repeated system prompt to shared prefix",
      "description": "The system prompt (840 tokens) is repeated in all 5 calls. Extract it as a cached prefix.",
      "estimated_savings_tokens": 3360,
      "estimated_savings_percentage": 22.4,
      "priority": "high",
      "before_snippet": "Call 1: [system] You are...\nCall 2: [system] You are...",
      "after_snippet": "[cached prefix] You are...\nCall 1: [user] ...\nCall 2: [user] ..."
    }
  ],
  "total_estimated_savings_tokens": 5120,
  "total_estimated_savings_percentage": 34.1
}
```

## 6.6 Optimized Prompt Structure

- **Meaning:** A restructured version of the input that maximizes cacheability
- **Calculation:** Apply all suggestions to produce an optimized layout
- **UI display:** Side-by-side or tabbed view: "Original" vs "Optimized." Color-coded sections. Copy button for optimized version.
- **CLI JSON:**
```json
{
  "optimized_structure": {
    "description": "Restructured for optimal prefix caching",
    "messages": [
      {"role": "system", "content": "...", "section_type": "static"},
      {"role": "user", "content": "{{user_input}}", "section_type": "dynamic"}
    ],
    "estimated_tokens_per_call": 1200,
    "original_tokens_per_call": 1840,
    "savings_per_call": 640
  }
}
```

---

# 7. User Flows

## Flow 1: First-Time UI User

| Step | User Intent | System Behavior |
|------|-------------|-----------------|
| 1 | Launch tool | `uvx tokenlens ui` → opens browser at `localhost:8420`. Show landing page with input area. |
| 2 | Understand what to do | Landing page shows: large text area with placeholder "Paste a prompt, message chain, or JSON trace...", plus a "Load example" link and a file upload button. |
| 3 | Try with example | User clicks "Load example" → pre-populated example trace appears in text area. |
| 4 | Run analysis | User clicks "Analyze" button → 1-3 second processing → redirect to results view. |
| 5 | Understand results | Results page shows: score at top, waste summary, then detailed sections. User scans top-down. |
| 6 | Act on suggestions | User reads suggestion cards, copies optimized structure. |

**Edge cases:**
- Empty input → "Analyze" button disabled. Placeholder text visible.
- Invalid JSON → Treat as raw text. Show info banner: "Treated as plain text. For structured analysis, use JSON format."
- Very large input (>500K tokens) → Show warning: "Large input detected. Analysis may take a moment." Process anyway.

**Empty state:** Landing page is the empty state. No "no data" screen needed.

**Failure state:** If analysis fails (malformed data that passes validation but breaks engine), show: "Analysis failed. Try simplifying your input or check the format." with a "Report Issue" link.

## Flow 2: Returning UI User

| Step | User Intent | System Behavior |
|------|-------------|-----------------|
| 1 | Launch tool | Same as above. No login, no saved state in V1. |
| 2 | Paste new input | Text area is empty and ready. Browser may have last input in clipboard. |
| 3 | Analyze | Same flow. |

V1 does not persist analyses between sessions. The tool is stateless.

## Flow 3: CLI / Agent User

```bash
# Analyze a file
uvx tokenlens analyze trace.json

# Analyze with JSON output
uvx tokenlens analyze trace.json --format json

# Analyze from stdin
cat trace.json | uvx tokenlens analyze --format json

# Analyze raw text
uvx tokenlens analyze prompt.txt

# Get just the score
uvx tokenlens analyze trace.json --format json | jq '.cacheability_score'
```

| Step | User Intent | System Behavior |
|------|-------------|-----------------|
| 1 | Run analysis | Parse args, detect input format, run engine. |
| 2 | Read output | Default: human-readable summary to stdout. `--format json`: full JSON to stdout. |
| 3 | Use output | Agent parses JSON. Human reads summary. |

**Edge cases:**
- File not found → stderr: `Error: File not found: trace.json`, exit code 1
- Empty file → stderr: `Error: Input is empty`, exit code 1
- Invalid JSON (when `.json` extension) → stderr: `Warning: File has .json extension but content is not valid JSON. Treating as plain text.`
- stdin with no data and no file arg → stderr: `Error: No input provided. Pass a file path or pipe input via stdin.`, exit code 1

---

# 8. Screens and UX

## Screen 1: Landing / Input Screen

**Purpose:** Get input from the user as fast as possible.

**Layout:**
```
┌─────────────────────────────────────────────┐
│  TokenLens                    [Load Example] │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │                                        │  │
│  │  Paste a prompt, prompt chain,         │  │
│  │  or JSON trace...                      │  │
│  │                                        │  │
│  │                                        │  │
│  │                                        │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  [Upload File]           [Analyze →]         │
│                                              │
│  Supports: raw text, message arrays,         │
│  multi-call traces (JSON)                    │
└─────────────────────────────────────────────┘
```

**Key components:**
- App title (no logo needed — text is fine)
- Textarea (monospace, resizable, minimum 12 lines)
- "Load Example" link (top right, loads a built-in trace)
- "Upload File" button (accepts `.json`, `.txt`, `.jsonl`)
- "Analyze" button (primary action, right-aligned, disabled when textarea empty)
- Format hint text below

**States:**
- Empty: Placeholder text visible, Analyze disabled
- With content: Analyze enabled, character/token count shown below textarea
- Loading: Analyze button shows spinner, textarea disabled
- Error: Red banner above textarea with error message

## Screen 2: Results Screen

**Purpose:** Show the analysis in a scannable, top-down hierarchy.

**Layout:**
```
┌─────────────────────────────────────────────┐
│  TokenLens    [← New Analysis]              │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │  Cacheability Score: 62 / 100        │   │
│  │  ████████████░░░░░░░░  Good          │   │
│  │  15,000 input tokens · 34% waste     │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  TOP WASTE SOURCES                           │
│  ┌──────────────────────────────────────┐   │
│  │ 1. Repeated system prompt (3,360 tk) │   │
│  │ 2. Misplaced dynamic block (980 tk)  │   │
│  │ 3. Fragmented static blocks (780 tk) │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  STATIC vs DYNAMIC                           │
│  ┌──────────────────────────────────────┐   │
│  │ ████████████████░░░░░░░░░░░          │   │
│  │ Static: 57%         Dynamic: 43%     │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  SUGGESTIONS                                 │
│  ┌──────────────────────────────────────┐   │
│  │ [1] Extract repeated system prompt   │   │
│  │     Est. savings: 3,360 tokens       │   │
│  │     [Show details ▼]                 │   │
│  ├──────────────────────────────────────┤   │
│  │ [2] Reorder: move static before...   │   │
│  │     Est. savings: 980 tokens         │   │
│  │     [Show details ▼]                 │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  OPTIMIZED STRUCTURE                         │
│  ┌──────────────────────────────────────┐   │
│  │  [Original] [Optimized]              │   │
│  │  ┌──────────────────────────────┐    │   │
│  │  │ system: "You are..." (static)│    │   │
│  │  │ user: "{{input}}" (dynamic)  │    │   │
│  │  └──────────────────────────────┘    │   │
│  │                        [Copy JSON]   │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  [Export JSON]  [← New Analysis]             │
└─────────────────────────────────────────────┘
```

**Key components:**
- Score card: large number, progress bar, label, summary stats
- Waste sources: numbered list, each with type badge and token count
- Static/dynamic bar: horizontal stacked bar
- Suggestions: expandable cards with before/after and savings
- Optimized structure: tab toggle (original/optimized), copy button
- Export JSON button: downloads full analysis as JSON
- New Analysis button: returns to input screen

**States:**
- Loading: Skeleton placeholders for each section
- No waste found: Score shows 90+, waste section says "No significant waste detected. Your prompts are well-structured."
- Single prompt (no multi-call): Hide "repeated blocks" section, focus on structure analysis

## Screen 3: CLI Output (Human-Readable Mode)

```
TokenLens Analysis
══════════════════

Score: 62 / 100 (Good)
Total input tokens: 15,000
Estimated waste: 5,120 tokens (34.1%)

Top Waste Sources
─────────────────
 1. Repeated system prompt .............. 3,360 tokens
 2. Dynamic content before static ....... 980 tokens
 3. Fragmented static blocks ............ 780 tokens

Suggestions
───────────
 [HIGH] Extract repeated system prompt to shared prefix
        → Save ~3,360 tokens/workflow

 [MED]  Move static instructions before dynamic content
        → Save ~980 tokens/call

Run with --format json for machine-readable output.
Run with --suggestions to see full restructured prompts.
```

---

# 9. Information Architecture

**App type:** Single-page application with two views (input → results). No routing needed. Can use simple state toggle.

**Navigation:**
- Input view → click "Analyze" → Results view
- Results view → click "New Analysis" → Input view (clears state)
- No sidebar, no menu, no settings page in V1

**Local-first behavior:**
- Everything in-process. No API calls.
- No persistence in V1. Each session is fresh.
- Browser localStorage could optionally hold the last analysis for convenience (V1.1).

**Saved analyses:** Not in V1. Users can export JSON manually.

---

# 10. CLI Design

## Primary Command

```
tokenlens <command> [options]
```

## Commands

| Command | Description |
|---------|-------------|
| `analyze <file>` | Analyze a prompt, chain, or trace file |
| `ui` | Launch the local web UI |
| `version` | Print version |

## `analyze` Subcommand

```
tokenlens analyze <file> [options]

Arguments:
  file              Path to input file (or - for stdin)

Options:
  --format <fmt>    Output format: human (default), json
  --suggestions     Show full suggestion details (human mode)
  --score-only      Print only the cacheability score
  --min-tokens <n>  Minimum token threshold for repeated block detection (default: 50)
  --encoding <enc>  Tokenizer encoding (default: cl100k_base)
```

## `ui` Subcommand

```
tokenlens ui [options]

Options:
  --port <port>     Port to serve on (default: 8420)
  --no-open         Don't auto-open browser
```

## Examples

```bash
# Basic analysis
uvx tokenlens analyze my-trace.json

# JSON output for scripting
uvx tokenlens analyze my-trace.json --format json

# Pipe from stdin
cat prompt.txt | uvx tokenlens analyze - --format json

# Just the score
uvx tokenlens analyze my-trace.json --score-only

# Full details
uvx tokenlens analyze my-trace.json --suggestions

# Launch UI
uvx tokenlens ui
uvx tokenlens ui --port 9000
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Input error (file not found, empty, parse error) |
| 2 | Analysis error (engine failure) |

## JSON Output Schema (top level)

```json
{
  "version": "1.0.0",
  "input_type": "multi_call_trace",
  "total_input_tokens": 15000,
  "total_calls": 5,
  "cacheability_score": 62,
  "cacheability_label": "Good",
  "score_breakdown": { ... },
  "waste_summary": {
    "total_waste_tokens": 5120,
    "waste_percentage": 34.1,
    "sources": [ ... ]
  },
  "static_dynamic_breakdown": { ... },
  "repeated_blocks": [ ... ],
  "suggestions": [ ... ],
  "optimized_structure": { ... }
}
```

---

# 11. Data Model

## Core Entities

### AnalysisInput

```json
{
  "input_type": "raw_text | prompt_chain | multi_call_trace",
  "raw_content": "string (original input)",
  "calls": [
    {
      "call_id": "string | null",
      "messages": [
        {
          "role": "string",
          "content": "string",
          "token_count": 840
        }
      ],
      "model": "string | null",
      "usage": {
        "input_tokens": 1500,
        "output_tokens": 300,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0
      }
    }
  ]
}
```

For raw text and prompt chains, normalize into a single-element `calls` array internally.

### AnalysisResult

```json
{
  "version": "1.0.0",
  "timestamp": "2026-03-11T00:00:00Z",
  "input_type": "multi_call_trace",
  "input_summary": {
    "total_calls": 5,
    "total_messages": 15,
    "total_input_tokens": 15000
  },
  "cacheability_score": 62,
  "cacheability_label": "Good",
  "score_breakdown": {
    "static_prefix_penalty": -10,
    "repetition_penalty": -15,
    "interleave_penalty": -5,
    "no_prefix_penalty": 0,
    "fragmentation_penalty": -8
  },
  "waste_summary": {
    "total_waste_tokens": 5120,
    "waste_percentage": 34.1,
    "sources": []
  },
  "static_dynamic_breakdown": {
    "total_static_tokens": 8500,
    "total_dynamic_tokens": 6500,
    "static_percentage": 56.7,
    "sections": []
  },
  "repeated_blocks": [],
  "suggestions": [],
  "optimized_structure": null
}
```

### RepeatedBlock

```json
{
  "content_preview": "string (first 200 chars)",
  "content_full": "string",
  "content_hash": "sha256:...",
  "occurrences": 5,
  "tokens_per_occurrence": 840,
  "total_waste_tokens": 3360,
  "locations": [
    {"call_index": 0, "message_index": 0, "role": "system"}
  ]
}
```

### WasteSource

```json
{
  "type": "repeated_block | misplaced_dynamic | interleaved | oversized_context | redundant_instructions",
  "description": "string",
  "waste_tokens": 3360,
  "percentage_of_total": 22.4,
  "priority_score": 3360.0,
  "related_block_hash": "sha256:... | null"
}
```

### Suggestion

```json
{
  "id": "s1",
  "type": "consolidate_repeated | reorder_prefix | merge_statics | extract_template | trim_redundant",
  "title": "string",
  "description": "string",
  "priority": "high | medium | low",
  "estimated_savings_tokens": 3360,
  "estimated_savings_percentage": 22.4,
  "before_snippet": "string",
  "after_snippet": "string"
}
```

### Section (Static/Dynamic)

```json
{
  "classification": "static | dynamic",
  "confidence": 0.95,
  "token_count": 840,
  "content_preview": "string (first 200 chars)",
  "position": "prefix | middle | suffix",
  "source_location": {"call_index": 0, "message_index": 0}
}
```

---

# 12. Technical Implementation Guidance

## Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | `tiktoken` is Python-native. `uvx` distribution. Fast to build. |
| CLI framework | `click` | Simple, well-documented, decorator-based. |
| Tokenizer | `tiktoken` | Standard, fast, accurate for cl100k_base. |
| Web UI server | `FastAPI` with `uvicorn` | Lightweight, async, serves both API and static files. |
| Frontend | Vanilla HTML + CSS + minimal JS (or `htmx`) | No build step. Ships as static files inside the Python package. |
| Packaging | `pyproject.toml` with `hatchling` | Modern Python packaging. `uvx` compatible. |
| Similarity | `difflib.SequenceMatcher` | Standard library. Good enough for block comparison. |

## Why not React/Next.js for the UI

The UI is two screens with no complex state. A build step adds complexity for the solo developer and makes packaging harder. Vanilla HTML with `htmx` for the analyze → results transition is simpler and ships inside the Python package as static assets.

## Architecture

```
tokenlens/
├── __init__.py
├── __main__.py          # Entry point
├── cli.py               # Click CLI
├── server.py            # FastAPI server for UI
├── engine/
│   ├── __init__.py
│   ├── analyzer.py      # Main analysis orchestrator
│   ├── tokenizer.py     # Token counting wrapper
│   ├── repeats.py       # Repeated block detection
│   ├── classifier.py    # Static/dynamic classification
│   ├── scorer.py        # Cacheability scoring
│   ├── waste.py         # Waste source detection
│   ├── suggestions.py   # Suggestion generation
│   └── optimizer.py     # Optimized structure generation
├── models.py            # Pydantic data models
├── parser.py            # Input parsing and normalization
├── static/              # HTML, CSS, JS for UI
│   ├── index.html
│   ├── style.css
│   └── app.js
└── examples/            # Built-in example traces
    └── agent-loop.json
```

## Keeping it local-first

- The web UI is served by a local process. No external requests.
- `tiktoken` downloads its encoding file once and caches it locally.
- No telemetry, no analytics, no external calls.

## Keeping it privacy-friendly

- Input is never sent anywhere. All processing is in-process.
- No persistence by default. Analysis results exist only in memory during the session.
- No cookies, no user tracking, no accounts.

## What to hardcode initially

- Encoding: `cl100k_base` only. Add encoding flag but don't implement others in V1.
- Example trace: One built-in example JSON file.
- Suggestion templates: Hardcoded suggestion text templates in `suggestions.py`.

## Key libraries

| Library | Purpose |
|---------|---------|
| `tiktoken` | Token counting |
| `click` | CLI |
| `fastapi` | Web server |
| `uvicorn` | ASGI server |
| `pydantic` | Data models and validation |
| `difflib` | Text similarity |
| `jinja2` | HTML templating (optional, for server-rendered UI) |

---

# 13. OSS Product Strategy

## Why open source

- The core analysis is deterministic rules — no proprietary moat to protect.
- Trust matters: users are pasting their prompts. They need to see the code.
- Community can contribute new waste patterns and heuristics.
- Open source is the distribution strategy for developer tools.

## What's open in the core repo

Everything. The full CLI, UI, engine, and examples. MIT license.

## What can be paid later (not in V1)

| Feature | Type | Timing |
|---------|------|--------|
| Hosted web version (paste & analyze without install) | SaaS | V2 |
| Team shared analysis dashboard | SaaS | V2+ |
| CI integration (GitHub Action) | Free core, paid reporting | V2 |
| Historical tracking and trend graphs | Local premium or hosted | V2+ |
| Provider-specific optimization (Anthropic cache headers, OpenAI cache hints) | Open core | V1.1 |

## Repo structure

```
tokenlens/
├── README.md
├── LICENSE (MIT)
├── pyproject.toml
├── src/
│   └── tokenlens/
│       └── (source code)
├── tests/
├── examples/
├── docs/
│   ├── input-formats.md
│   └── scoring.md
└── CONTRIBUTING.md
```

## Making it contributor-friendly

- Clear `CONTRIBUTING.md` with setup instructions
- Each engine module is independent and testable
- Adding a new waste pattern = adding a function + test
- `examples/` directory with sample traces for testing
- GitHub Issues with "good first issue" labels

## Monetization

- **GitHub Sponsors** on the repo and maintainer profile
- **Ko-fi** link in README and UI footer
- **Hosted version** later (optional paid tier for teams)
- **Consulting** on prompt optimization for companies (the tool is the lead gen)

---

# 14. Non-Goals and Anti-Patterns

**Do not turn this into a generic observability platform.** TokenLens analyzes prompts for token efficiency. It does not monitor latency, track errors, display dashboards of API call volumes, or replace Langfuse/LangSmith/Helicone. If someone asks for real-time monitoring, the answer is "use an observability tool and export traces to TokenLens for optimization analysis."

**Do not become an enterprise compliance platform.** No PII detection, no policy enforcement, no audit trails, no role-based access control. These are real needs but they are different products.

**Do not overbuild integrations.** V1 accepts JSON. That's the integration. Do not build OpenAI SDK middleware, LangChain callbacks, or Anthropic client wrappers until the core analysis is proven and users are requesting specific integrations.

**Do not try to support every trace format.** V1 supports the message-array format defined in Section 4. Provider-specific formats (LangSmith traces, Langfuse generations, Helicone logs) are V2 import adapters.

**Do not use LLMs in the analysis pipeline.** The analysis engine must be deterministic, fast, free, and reproducible. "Use GPT-4 to suggest optimizations" defeats the purpose (costs tokens to save tokens). Rules-based analysis first. LLM-powered suggestions can be an optional add-on later.

**Do not build a prompt editor.** TokenLens analyzes and suggests. It does not need to be a place where users write and iterate on prompts. Show the optimized structure; let users copy it to their own tools.

**Do not add user accounts, authentication, or cloud storage in V1.** The tool is local and stateless. That's a feature, not a limitation.

---

# 15. Phased Roadmap

## V1 — Core Analysis Tool

**What ships:**
- CLI: `tokenlens analyze` with human and JSON output
- Web UI: paste/upload → analyze → results (two screens)
- Engine: repeated block detection, static/dynamic classification, cacheability score, waste ranking, restructuring suggestions
- One built-in example trace
- `uvx tokenlens` distribution

**Why it matters:** This is the complete core product. A user can go from "I wonder if my prompts are wasteful" to "here's exactly what to fix and how" in under 30 seconds.

**Complexity:** Moderate. The engine has 6 modules but each is self-contained. The UI is two screens with no state management.

## V1.1 — Polish and Patterns

**What gets added:**
- Provider-specific cache awareness (Anthropic `cache_control` block detection, OpenAI automatic caching heuristics)
- Additional waste patterns: near-duplicate detection using cosine similarity on token sequences
- `--watch` mode: re-analyze when file changes
- UI: syntax highlighting in the content viewer
- 3-5 built-in example traces covering common patterns
- `tokenlens compare trace1.json trace2.json` — compare two analyses

**Why it matters:** Makes the tool more useful for real workflows without changing the architecture.

**Complexity:** Low-moderate. Each addition is independent.

**Should it wait?** Yes. V1 must ship and get user feedback first.

## V2 — Integrations and History

**What gets added:**
- Import adapters: LangSmith, Langfuse, Helicone, OpenAI log format
- Local history: SQLite storage of past analyses with trend tracking
- CI mode: `tokenlens ci --threshold 60` (exits non-zero if score below threshold)
- GitHub Action wrapper
- Optional hosted version (analyze via web without installing)
- Team sharing (export/import analysis bundles)

**Why it matters:** Moves from "developer tool" to "development workflow tool."

**New complexity:** Persistence layer, import adapters, CI integration, hosting infrastructure.

**Should it wait?** Absolutely. Only build this after V1 has real users and validated demand for specific integrations.

---

# 16. Final Handoff Section

## BUILD HANDOFF

### Build Summary

TokenLens is a Python CLI + local web tool. It takes AI prompts/traces as input, runs a deterministic rules-based analysis engine, and outputs a cacheability score, waste breakdown, and concrete restructuring suggestions. No external dependencies beyond `tiktoken`. No backend infrastructure. Ships via `uvx`.

### Exact MVP to implement first

1. Input parser (`parser.py`) — detect format, normalize to internal `Call[]` model
2. Token counter (`tokenizer.py`) — thin wrapper around `tiktoken`
3. Repeated block detector (`repeats.py`) — the highest-impact analysis module
4. Static/dynamic classifier (`classifier.py`) — diff-based for multi-call, heuristic for single
5. Cacheability scorer (`scorer.py`) — the formula from Section 5.4
6. Waste ranker (`waste.py`) — aggregate findings into ranked list
7. Suggestion generator (`suggestions.py`) — rule-based templates
8. CLI (`cli.py`) — `analyze` command with `--format json` and human output
9. Web UI (`server.py` + `static/`) — two-screen SPA served by FastAPI

Build in this order. Each module is independently testable.

### Single most important screen

**The Results screen.** This is where the user gets value. The input screen is trivial (textarea + button). The results screen must clearly show: score, waste sources, and what to do about them. If the results screen is confusing, nothing else matters.

### Single most important command

```bash
uvx tokenlens analyze trace.json --format json
```

This is the agent-mode entry point. If this works correctly and outputs valid JSON matching the schema in Section 10, the tool is useful for both humans and automated workflows.

### Single most important analysis function

**Repeated block detection** (`find_repeated_blocks`). This catches the biggest and most common waste source: system prompts and instructions copy-pasted across every call in an agent loop. If only one analysis module works, this one delivers the most value.

### Biggest implementation risk

**Block segmentation quality.** The repeated block detector depends on how content is split into comparable blocks. If blocks are too large, it misses partial repeats. If too small, it generates noise. Start with paragraph-level splitting (double newline), fall back to sentence-level for short content, and use a 50-token minimum threshold. Test against 3-5 real-world traces during development to calibrate.

### Best way to keep scope under control

Implement the engine modules one at a time, each with tests. Get `tokenlens analyze` working with JSON output before touching the web UI. The CLI is the source of truth — the UI is just a wrapper that calls the same engine. If a feature doesn't improve the output of `tokenlens analyze trace.json --format json`, it doesn't belong in V1.
