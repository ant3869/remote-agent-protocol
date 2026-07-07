# Mission Brief: Multi-Wake-Word Persona Routing

## Objective
Currently, the system uses a single optional wake-word gate (`WAKE_WORD_ENABLED=true` via openwakeword). We want to support listening for multiple wake words simultaneously, where each wake word automatically switches the active persona and voice profile dynamically.

## Requirements
- Modify the `openwakeword` integration in the Pipecat audio pipeline to load and monitor multiple models concurrently (e.g., "hey jarvis" and "hey jess").
- Create a configuration mapping (in `.env` or `config.json`) that links a specific wake phrase to a specific persona/TTS voice profile.
- When a specific wake word is triggered, intercept the event and trigger a live persona/voice switch *before* passing the subsequent speech to the STT/Ollama pipeline.

## Success Criteria
- The user can say "Hey Jarvis, what time is it?" and get a response in the Jarvis persona.
- The user can say "Hey Jess, what's my schedule?" and get a response in the Jess persona.
- The transitions occur without requiring any manual `Ctrl+L` or GUI clicking.