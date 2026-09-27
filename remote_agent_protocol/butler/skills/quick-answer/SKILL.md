---
name: quick-answer
description: A quick factual question (weather, news, prices, versions, opening hours, a definition) that doesn't need an agent
---

# Quick answers

1. If it's general knowledge that doesn't change, just answer.
2. If it's current or specific, use `web_search` (or `read_page` for a link the
   user gave) instead of starting an agent: it's seconds instead of minutes.
3. Answer in one or two sentences and say where it came from ("according to the
   BBC").
4. If the lookup fails or the answer needs real work (comparing many sources,
   writing something), offer to start a task instead.
