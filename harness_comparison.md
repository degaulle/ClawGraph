# How Claude Code and OpenAI Codex actually work under the hood

**Both tools share the same fundamental architecture — a deceptively simple while-loop that calls an LLM, executes any requested tools, and feeds results back until the model stops asking for tools.** The devil is in the details: how each system assembles prompts, manages context windows, sandboxes execution, and orchestrates multi-step tasks. Claude Code is a **512,000-line TypeScript codebase** running on Bun with React-based terminal UI, while Codex CLI is a **Rust binary** using the OpenAI Responses API. Both are now open source, and both have been extensively analyzed — Claude Code through a source map leak on March 31, 2026, and Codex through its Apache 2.0 GitHub repository.

---

## The agentic loop: same skeleton, different muscles

At their core, both tools implement what software engineers would recognize as a classic **tool-use agent loop**. The pseudocode is nearly identical:

```
while true:
    response = call_model(system_prompt, tools, message_history)
    if response.has_tool_calls:
        results = execute_tools(response.tool_calls)
        message_history.append(response + results)
    else:
        return response.text  # turn complete
```

**Claude Code** implements this in `query.ts` and `QueryEngine.ts` (together ~47,000 lines). The loop runs single-threaded with what's internally codenamed "nO" — a flat message history that accumulates tool calls and results across turns. A critical addition is the **h2A queue**, an asynchronous dual-buffer system that lets users inject new instructions mid-execution without restarting the loop. When a tool call arrives, a `StreamingToolExecutor` partitions calls into read-only batches (run concurrently, up to 10 in parallel) and write batches (run serially), meaning the agent can grep five files simultaneously but applies edits one at a time.

**OpenAI Codex** implements its loop in `codex-rs/core/`, making HTTP POST requests to the `/responses` endpoint. Each "turn" can include dozens of inference-tool cycles before returning control to the user. A key architectural difference: Codex maintains conversation state **client-side** in the prompt rather than server-side, though it can use `previous_response_id` to chain responses without re-sending full history. The **App Server** — a bidirectional JSON-RPC 1.0 API over JSONL-stdio or WebSocket — wraps this loop and exposes it to all surfaces (CLI, VS Code extension, macOS desktop app, cloud), meaning every Codex product shares the exact same harness.

Both tools continue looping until the model produces a response without tool calls. Both inject invisible continuation messages when the model hits output token limits — Claude Code's reads *"Resume directly — no apology, no recap"* and allows **3 consecutive recovery attempts** before surfacing the error.

---

## Models powering each tool

Claude Code defaults to **Claude Sonnet 4.6** (`claude-sonnet-4-6`) for most users, with Claude Opus 4.6 available via `--model` flag or the `/model` slash command. The system prompt explicitly states the model identity and notes that "Fast mode" (internally called "Penguin Mode") uses the same Opus 4.6 model with faster output, not a different model. Claude Code also uses a **lightweight model** (such as Haiku) for metadata tasks like conversation summarization and codebase exploration sub-agents, creating a dual-model architecture observed through LLM traffic tracing.

Codex's model lineage is more complex. The original **codex-1** (May 2025) was a version of o3 optimized via reinforcement learning on real coding tasks. The current default is **GPT-5.3-Codex** with **GPT-5.4** as the recommended upgrade — it uses "medium" reasoning effort by default and supports hours-long autonomous execution. A fast variant called **GPT-5.3-Codex-Spark** exists for ChatGPT Pro users. All models support the Responses API and can be switched mid-session.

---

## System prompts: philosophically aligned, structurally different

Both system prompts share a strikingly similar philosophy — concise, direct, technically accurate, keep going until the task is done. But their construction differs significantly.

**Claude Code's system prompt** is approximately **76KB** of dynamically assembled text. It is not a single string but modular cached sections split by a `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` marker into static portions (cacheable across organizations for prompt caching efficiency) and dynamic portions (user/session-specific). Key directives include:

```
You are an interactive CLI tool that helps users with software engineering tasks.
NEVER create files unless they're absolutely necessary. ALWAYS prefer editing existing files.
Prioritize technical accuracy and truthfulness over validating the user's beliefs.
Don't add features, refactor code, or make "improvements" beyond what was asked.
```

The prompt appends environment context (working directory, git status, platform, shell) and includes security instructions owned by Anthropic's Safeguards team prohibiting assistance with destructive techniques while permitting authorized security testing.

**Codex's system prompt** lives at `codex-rs/core/prompt.md` in the open-source repo and is notably shorter:

```
You are a coding agent running in the Codex CLI, a terminal-based coding assistant.
You are expected to be precise, safe, and helpful.
Please keep going until the query is completely resolved, before ending your turn.
Autonomously resolve the query to the best of your ability. Do NOT guess or make up an answer.
```

Codex structures its full prompt through the Responses API's **role-based message assembly**: system role (OpenAI-injected model preambles), tool definitions, instructions (from `config.toml` + model-specific base prompts), developer role messages (sandbox permissions, `AGENTS.md` project files), and finally user role messages (CWD, shell environment, actual query). This separation lets OpenAI inject model-specific preambles server-side while clients control task context.

---

## Tool arsenals: Claude Code's breadth versus Codex's training depth

**Claude Code exposes 60+ tools**, though only a subset is active in any session — approximately 18 tools are hidden by default and loaded on-demand via `ToolSearchTool` when the model explicitly searches for them. Tools are sorted alphabetically to maximize prompt cache hit rates. The complete toolkit spans file operations (`Read`, `Edit`, `Write`), discovery (`Glob`, `Grep` wrapping ripgrep, `LS`), execution (`BashTool` with persistent shell sessions), web access (`WebFetch`, `WebSearch`), LSP integration (9 operations including go-to-definition, find-references, hover), subagent management (`AgentTool`, `TaskCreate/Get/List/Update/Stop`), and specialized tools like `NotebookEditTool` for Jupyter notebooks. Each tool conforms to a generic `Tool<Input, Output, Progress>` interface with Zod schema validation.

The **BashTool** is particularly sophisticated: it classifies commands by risk level, limits output to **30,000 characters** (saving overflow to disk with a preview sent to the model), auto-backgrounds commands exceeding 15 seconds, and filters injection patterns blocking backticks and `$()` constructs.

**Codex takes a different approach with fewer but more deeply integrated tools.** Its core tools are `shell` (terminal execution), `apply_patch` (a first-class diff/patch tool the model was **specifically trained to excel at**), `read_file`, `update_plan`, web search, and image handling. The `apply_patch` tool is Codex's signature — rather than Claude Code's search-and-replace `FileEditTool`, Codex models are RL-trained to produce unified diff patches, making edits more precise for large changes. OpenAI strongly recommends using their exact implementation, available both as a Responses API native tool and via context-free grammar enforcement.

Both tools support **MCP (Model Context Protocol)** for extensibility. Codex functions as both an MCP client and server (`codex mcp-server`), while Claude Code operates primarily as an MCP client with 5 transport types supported.

| Capability | Claude Code | OpenAI Codex |
|-----------|-------------|--------------|
| File editing | Search-and-replace (`old_string` → `new_string`) | Unified diff patches (model RL-trained) |
| Shell execution | Persistent sessions, 30K char limit, injection filtering | Sandboxed with OS-native isolation |
| Code navigation | Built-in LSP (9 operations) | Via shell tools (`rg`, language servers) |
| Web access | `WebFetch` + `WebSearch` (max 8/invocation) | Cached or live web search |
| Subagents | `AgentTool` with depth limits, 4 specialist types | Path-addressed subagents, max 6 concurrent |
| Hidden tools | ~18 deferred-loaded via `ToolSearchTool` | All tools always available |

---

## Context management: compression versus caching

Managing the finite context window is perhaps the most critical engineering challenge for agentic coding tools, and the two systems take meaningfully different approaches.

**Claude Code** operates within a **~200K token context window** (with a 1M token beta on Sonnet 4.6). It employs a three-layer compression strategy. **MicroCompact** performs local edits to cached content without API calls. **AutoCompact** triggers at approximately **83.5% context utilization** (configurable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`), generates up to 20,000-token summaries while reserving a 13,000-token buffer, and typically achieves **60-80% reduction** — a 150K context might compact to 30-50K. A circuit breaker stops after 3 consecutive failures. Manual compaction via `/compact` accepts focus instructions (e.g., `/compact focus on the API changes`). Long-term memory uses **CLAUDE.md markdown files** at project root — deliberately avoiding vector databases or embeddings. An `autoDream` background process performs memory consolidation during idle periods, merging observations and converting insights into facts.

**Codex** leans heavily on **prompt caching** rather than compression. The Responses API achieves linear-time model sampling despite growing payloads by matching exact prefixes against cached previous inferences. Static content (instructions, tool definitions, sandbox config) is placed at the **beginning** of prompts, with variable user messages appended at the end. Configuration changes mid-conversation are appended as new messages rather than modifying early segments to preserve cache. When caching isn't enough, Codex uses a dedicated **`/responses/compact` endpoint** that returns special `type=compaction` items with **encrypted content** maintaining the model's latent understanding without full message history — a fundamentally different approach from Claude Code's summarization. Codex also uses `AGENTS.md` files (analogous to CLAUDE.md) for project memory, discovered by walking from project root to CWD with closer files taking precedence, capped at **32 KiB**.

---

## Sandboxing: containers versus OS-native isolation

The security architectures reflect fundamentally different platform philosophies.

**Claude Code** classifies every tool action as **LOW/MEDIUM/HIGH risk** and offers five public permission modes: `default` (ask for destructive operations), `plan` (read-only), `acceptEdits` (auto-approve edits, ask for shell), `bypassPermissions` (full access for CI), and `dontAsk` (auto-deny unsafe). Two internal modes exist: `auto` (an ML classifier evaluates each command) and `bubble` (delegation to parent agent). Permission rules form a priority cascade across user settings, project settings, local settings, CLI arguments, and session overrides, supporting glob patterns like `Bash(git push*)`.

**Codex** implements **OS-native sandboxing** — a more aggressive security posture. On macOS, it uses Apple's **Seatbelt** (`sandbox-exec`) with dynamically generated Sandbox Profile Language scripts. On Linux, it combines **Bubblewrap (bwrap)** for filesystem namespacing with **seccomp** for syscall filtering, running through a two-stage process: outer stage (bwrap namespace) → inner stage (`PR_SET_NO_NEW_PRIVS` + seccomp) → final exec. Network is **disabled by default** (`CODEX_SANDBOX_NETWORK_DISABLED=1`), and `.git` directories are always read-only even in write-enabled modes. Three sandbox modes (`read-only`, `workspace-write`, `danger-full-access`) combine with three approval policies (`untrusted`, `on-request`, `never`) for fine-grained control. Every spawned subprocess inherits sandbox policies.

---

## API request anatomy compared

A Claude Code API request sends to Anthropic's Messages API:

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 64000,
  "system": "[76KB dynamically assembled prompt]",
  "tools": [{"name": "Bash", "input_schema": {...}}, ...],
  "messages": [{"role": "user", "content": "..."}, ...],
  "thinking": {"type": "adaptive"},
  "stream": true
}
```

Custom headers include `x-anthropic-billing-header` with version fingerprints and client attestation hashes, plus `X-Claude-Code-Session-Id` for proxy aggregation. Extended thinking uses **adaptive mode** where the model dynamically decides thinking depth based on query complexity and effort setting.

A Codex CLI API request sends to OpenAI's Responses API:

```json
{
  "instructions": "[config + model-specific prompts]",
  "tools": [{"type": "shell"}, {"type": "apply_patch"}, ...],
  "input": [
    {"role": "developer", "content": "[sandbox + AGENTS.md]"},
    {"role": "user", "content": "[CWD + query]"}
  ],
  "model": "gpt-5.3-codex",
  "store": true
}
```

Responses stream back via Server-Sent Events. The Responses API's role separation (system/developer/user) lets OpenAI inject model-specific preambles server-side while clients control only task context — a structural advantage for model-specific optimization that Anthropic's single system prompt field doesn't natively support.

---

## Conclusion: converging designs with diverging bets

These two tools have converged on remarkably similar high-level architectures — the single-threaded tool-use loop, project-level markdown memory files, tiered permission systems, subagent spawning, and auto-compaction — suggesting this design pattern is close to optimal for current LLM-based coding agents. The meaningful divergences reveal each company's strategic bets. **Anthropic bets on prompt engineering breadth**: a massive 76KB system prompt, 60+ tools with deferred loading for cache efficiency, and sophisticated multi-layer context compression. **OpenAI bets on model-level training depth**: fewer tools but RL-trained proficiency (especially `apply_patch`), OS-native sandboxing for stronger security guarantees, encrypted compaction tokens for more efficient context management, and the Responses API's role separation for model-specific optimization. Claude Code's TypeScript/React stack prioritizes rapid iteration and rich terminal UI, while Codex's Rust implementation prioritizes raw performance and memory safety. Both are evolving toward always-on autonomous modes — Claude Code's internal "KAIROS" daemon and Codex Cloud's parallel container execution — suggesting the next frontier is not the agent loop itself but persistent, background-running coding agents.