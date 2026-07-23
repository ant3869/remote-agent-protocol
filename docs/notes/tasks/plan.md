You are a senior voice UX engineer, wake-word systems developer, and frontend engineer. Improve the Wake Word mode so users always know which wake word is active, when it has been detected, how long the assistant will remain responsive after detection, and when the assistant returns to passive listening.

## Goal

Make Wake Word mode reliable, understandable, and polished.

The user should receive:
- clear visual confirmation of the currently selected wake word,
- clear visual confirmation when the wake word is detected,
- a mild audio confirmation when detection happens,
- a visible countdown or state indicator showing how long the assistant remains responsive,
- and automatic return to passive wake-word listening after the follow-up window expires.

Do not remove any existing voice, persona, memory, model, or settings logic. Add this behavior on top of the current system.

## Required Wake Word Behavior

### 1. Show the Active Wake Word

In the Wake Word UI/settings area, display:
- the currently selected wake word,
- the wake-word model/file being used,
- whether passive listening is active,
- whether the wake-word detector is loaded successfully,
- and any error if wake-word initialization fails.

Example UI states:
- “Wake Word: Jarvis”
- “Listening for wake word…”
- “Wake word detected”
- “Assistant responsive for 3s”
- “Returning to wake-word mode…”

If multiple wake words are available in the project directory, allow the user to select the active wake word from the existing wake-word files.

### 2. Visual Detection Confirmation

When the wake word is detected:
- change the mode/status indicator immediately,
- highlight the mic or assistant status area with a blue/cyan active state,
- show a temporary “Wake word detected” confirmation,
- show that the assistant is now listening for the user’s request,
- and display a countdown or progress indicator for the follow-up/listening window.

The user should never have to guess whether the wake word worked.

### 3. Mild Audio Confirmation

When the wake word is detected, play a soft, dull chime.

Requirements:
- the chime should be subtle and non-intrusive,
- it should not interrupt the user,
- it should not be read aloud by TTS,
- it should be configurable or easy to replace,
- it should respect mute/audio settings where appropriate,
- and it should only play after successful wake-word detection.

Do not use a harsh alert sound.

### 4. Responsive Window After Wake Word

After the wake word is detected, keep the assistant responsive for a short follow-up window.

Default behavior:
- keep the listening window open for **3 seconds** after wake-word detection,
- if the user starts speaking during that window, continue listening until the utterance is complete,
- send the captured utterance to the agent when speech ends,
- keep the window open again for follow-up after the model response completes,
- if no follow-up question or statement is detected, return to passive wake-word listening.

### 5. Follow-Up After Model Response

After the model finishes responding:
- keep the assistant responsive for another **3-second follow-up window**,
- show a visual countdown,
- listen for a follow-up question or statement,
- if a follow-up is detected, process it without requiring the wake word again,
- if no follow-up is detected within the window, return to passive wake-word waiting.

This should allow natural exchanges such as:
1. User says wake word.
2. Assistant chimes and listens.
3. User asks a question.
4. Assistant responds.
5. User has 3 seconds to ask a follow-up.
6. If no follow-up occurs, assistant returns to wake-word mode.

### 6. State Machine

Implement or update the Wake Word mode state machine with explicit states:

```ts
type WakeWordState =
  | "idle"
  | "loading_detector"
  | "waiting_for_wake_word"
  | "wake_word_detected"
  | "listening_for_command"
  | "transcribing"
  | "agent_responding"
  | "follow_up_window"
  | "returning_to_passive"
  | "error";