# Jess's Model Arsenal  

Every GGUF chat model found on `H:\Models`, mapped for use with Jess (the local
voice assistant). Jess talks to Ollama, so to use any model you first **register
it** with a Modelfile, then set `LLM_MODEL` in `.env` (or `remote_agent_protocol/config.py`).

## How to register & use a model

```bat
:: 1. Register (one-time). Modelfiles live in .\models\
ollama create <name> -f models\<name>.Modelfile

:: 2. Point Jess at it -- set in .env (or remote_agent_protocol/config.py):
::    LLM_MODEL=<name>

:: 3. Run
start_gui.bat        (desktop app)  or  start_terminal.bat  (terminal mode)
```

A Modelfile is just one line: `FROM H:\Models\path\to\model.gguf`

---

##  Flags legend
- ** uncensored** — abliterated / heretic / derestricted. No refusals.
- ** thinks-aloud** — reasoning model. Emits `<think>` blocks that TTS will
  SPEAK unless suppressed. **Avoid for voice** unless you add a reasoning
  filter (see note at bottom). Fine for text.
- ** vision** — has an `mmproj` file (can see images). Voice ignores that, but
  the text brain still works fine.
- ** code** — tuned for programming.
- ** registered** — already `ollama create`'d and ready right now.

---

## TIER 1 — Small & Fast (3–6 GB) · best for snappy voice latency

| Suggested name | Model | Size | Flags |
|---|---|---|---|
| `llama3.2:1b` | Llama 3.2 1B | 1.3 GB |  registered · tiny/instant |
| `nemotron-4b` | NVIDIA Nemotron-3 Nano 4B | 3.9 GB | clean, fast |
| `gemma-e4b-aggressive` | Gemma-4 E4B Uncensored HauhauCS Aggressive | 5.0 GB |  · vision |
| `gemma-e4b-max` | gemma-4-E4B-it Uncensored MAX | 4.8 GB |  |
| `gemma-e4b-q6` | gemma-4-E4B-it (lmstudio) Q6 | 5.8 GB | clean instruct |
| `dorna-llama8b` | Dorna2 Llama-3.1 8B Instruct | 4.6 GB | multilingual |
| `meta-llama-8b` | Meta Llama 3.1 8B Instruct | 4.6 GB |  registered? (has Modelfile) |
| `qwen3-4b-triplex` | Qwen3-4B TripleX Heretic | 4.0 GB |  ·  thinks-aloud |
| `deepseek-r1-llama8b` | DeepSeek-R1 Distill Llama 8B | 4.6 GB |  thinks-aloud |
| `deepseek-7b` | DeepSeek-R1 Distill Qwen 7B Uncensored | 4.1 GB |  ·  thinks · has Modelfile |
| `llama3-heretic` | Llama3.3 8B Thinking Heretic | 4.6 GB |  registered ·  thinks |
| `qwen3-8b` | Qwen3 8B | 4.7 GB |  thinks-aloud · has Modelfile |

## TIER 2 — Mid-weight (6–12 GB) · the sweet spot for quality + personality

| Suggested name | Model | Size | Flags |
|---|---|---|---|
| `granite-tiny` | IBM Granite 4.0-H Tiny | 6.9 GB | clean, efficient |
| `gemma-12b-qat` | gemma-4-12B-it QAT (lmstudio) | 6.5 GB | vision · efficient 12B |
| `gemma-12b-huihui` | Huihui gemma-4-12B abliterated | 6.9 GB |  · vision |
| `gemma-12b-duo` | DuoNeural Gemma4-12B Abliterated Q5 | 8.0 GB |  |
| `gemma-e4b-q8` | gemma-4-E4B-it Q8 (lmstudio) | 7.5 GB | vision |
| `huihui-e4b` | Huihui gemma-4-E4B abliterated Q8 | 7.5 GB |  · vision |
| `qwen35-9b` | Qwen3.5 9B (lmstudio) Q8 | 8.9 GB |  thinks-aloud · vision |
| `qwen35-9b-opus` | Qwen3.5-9B Opus Reasoning Distill v2 | 8.9 GB |  ·  thinks · vision |
| `qwen3-14b-opus` | Qwen3-14B Claude-4.5-Opus Distill | 8.4 GB |  thinks-aloud |
| `deckard-19b` | gemma-4-19B-A4B DECKARD Heretic (MoE) | 9.9 GB |  ·  thinks · vision |
| `gpt-oss-20b` | unsloth gpt-oss-20b Q4 | 10.8 GB |  thinks-aloud |
| `gemma-12b` | Gemma-4-12B AEON Abliterated Q8 | 11.8 GB |  registered ·  · vision |

## TIER 3 — Heavyweight (12 GB+) · max smarts, slower on your RTX 5060 Ti

| Suggested name | Model | Size | Flags |
|---|---|---|---|
| `qwopus-27b` | Huihui Qwopus3.5-27B abliterated Q3 | 12.4 GB |  ·  thinks · vision |
| `devstral-24b` | Devstral-Small-2 24B abliterated | 12.6 GB |  ·  code |
| `gpt-oss-derestricted` | gpt-oss-20b Derestricted | 13.6 GB |  ·  thinks-aloud |
| `hermes-20b` | OpenAI-20B NEO HERETIC Uncensored (CODE) | 14.6 GB |  registered ·  ·  code · **current default** |
| `qwen3-30b-moe` | Huihui Qwen3-30B-A3B abliterated (MoE) | 19.6 GB |  ·  thinks · vision |
| `qwen35-35b-moe` | Qwen3.5-35B-A3B (MoE) | 19.2 GB |  thinks-aloud · vision |

---

##  ralph's picks for Jess (a *voice* assistant)

Voice wants: conversational, low-latency, **doesn't think out loud**, has personality.

- **Snappiest** → `gemma-e4b-max` or `gemma-e4b-aggressive` (5 GB, uncensored, fast)
- **Best all-round** → `gemma-12b-huihui` (6.9 GB, uncensored 12B, much faster than the Q8 AEON default)
- **Current default** → `hermes-20b` (great brain, but 15 GB = slower first-token)
- **If you want her to code with you** → `devstral-24b`

I've pre-generated Modelfiles for the  voice-friendly picks (see `models\`).
Register any with `ollama create <name> -f models\<name>.Modelfile`.

---

##  Note on "thinks-aloud" models + voice
Reasoning models (Qwen3, DeepSeek-R1, gpt-oss, anything "Thinking") emit
`<think>...</think>` or analysis channels. In a *voice* pipeline TTS will read
that internal monologue aloud — weird and slow. Options:
- **Qwen3 / Qwen3.5**: add `/no_think` to the system prompt to disable reasoning.
- **gpt-oss**: set low `reasoning_effort`.
- Or just prefer a plain instruct model (Gemma / Llama / Granite) for voice.
A proper fix would be a pipeline frame-filter that strips `<think>` blocks before
TTS — tell ralph if you want that built.
