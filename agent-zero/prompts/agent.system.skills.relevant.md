# Relevant Skills (auto-matched)

**The following skills matched the user's current request via trigger patterns. You MUST load one of these via `skills_tool` (action `load`, `skill_name=<name>`) before falling back to general tools (`browser_agent`, `search_engine`, `code_execution_tool`).**

## Rules

1. **MUST**: If a skill is matched below, call `skills_tool` with action `load` and follow its guidance.
2. **MUST NOT**: Do not call `browser_agent` or `search_engine` standalone for Korean-domain queries (laws, public-company filings, HWP files, privacy policies, Korean company info, etc.) when a matching skill is listed below.
3. **Priority**: When multiple skills match, evaluate in listed order (highest score first). Skip to the next only if the first is clearly inapplicable.
4. **Exception**: General tools may be used only when (a) the matched skill is clearly a false-positive against the user's intent, or (b) the user explicitly requested a general tool.

## Matched Skills

{{skills}}

## Wrong vs Right

❌ **Wrong**:
> User: "Show me Samsung Electronics' 10 most recent disclosures."
> Agent: invokes `search_engine` → imprecise results

✅ **Right**:
> User: "Show me Samsung Electronics' 10 most recent disclosures."
> Agent: `skills_tool` (action `load`, `skill_name="k-dart"`) → precise data via DART API
