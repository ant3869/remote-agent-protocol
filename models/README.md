# Loading your own models

Ollama can serve any GGUF via its OpenAI-compatible API.
Each .Modelfile here registers one of your H:/Models GGUFs.

## One-time setup per model

```
cd "H:\Program Files (oss)\pipecat"
ollama create hermes-20b    -f models\hermes-20b.Modelfile
ollama create deepseek-7b   -f models\deepseek-7b.Modelfile
ollama create meta-llama-8b -f models\meta-llama-8b.Modelfile
ollama create qwen3-8b      -f models\qwen3-8b.Modelfile
ollama create llama3-heretic -f models\llama3-heretic.Modelfile
ollama create gemma-12b     -f models\gemma-12b.Modelfile
```

Then set `LLM_MODEL` in `.env` (or edit `jess/config.py`) to whichever name you registered.
Run `ollama list` to see everything that's loaded.

## Why every Modelfile here sets `num_ctx` / `num_predict`

A bare `FROM <gguf>` makes Ollama default to the model's **max context** (often
128K), which inflates the KV cache enough to spill the model onto the CPU on a
16GB GPU -> ~40s responses. `PARAMETER num_ctx 8192` keeps the whole model on
the GPU; `PARAMETER num_predict 160` caps spoken replies. If you re-register a
model, that's when these take effect (the live Ollama copy isn't updated until
you `ollama create` again).

## Thinking models

Many of these (gemma*, qwen3, deepseek, llama3-heretic, nemotron, hermes/gpt-oss)
emit a hidden `<think>` monologue that wrecks voice latency. Leave
`LLM_REASONING_EFFORT = "none"` in `jess/config.py` -- Ollama honours it on its /v1
endpoint and switches thinking off entirely (~0.4s replies instead of ~40s).

## To remove a model from Ollama (free up RAM, not disk)

```
ollama rm hermes-20b
```

The GGUF file on H: is untouched -- you can re-add it anytime.
