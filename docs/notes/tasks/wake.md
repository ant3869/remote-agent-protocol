````markdown
You are a senior AI product architect, voice UX engineer, and agent systems developer. Update the project so voice input behaves normally when no extra context is present, but intelligently waits and bundles voice with files, screenshots, links, images, or other context when those materials are detected. Also complete the wake-word integration and add a microphone mode selector.

## Goal

Create a voice input system that supports three modes:

1. **Wake Word**
2. **Free Talk**
3. **Push To Talk**

The user should be able to switch modes from a button next to the existing microphone mute button.

The voice system should feel natural:
- If I am just talking normally with no attached context, it should behave like normal voice chat.
- If I attach or reference a file, screenshot, image, link, or other context, the system should hold the voice input and send everything together as one unified prompt.
- The agent should never receive a partial prompt when voice and context belong together.

## Current Problem

The previous design made voice wait too broadly. That is not desired.

In **Free Talk mode**, voice should usually send normally. It should only pause, hold, or bundle the voice when the system detects that additional context is involved.

Examples of additional context:
- a screenshot was uploaded,
- an image was attached,
- a file was selected,
- a link was pasted,
- the user references “this image,” “the screenshot,” “the file,” “the link,” “the highlighted part,” “the attached thing,” etc.,
- a context card, upload, or attachment exists in the composer,
- the user is actively adding materials to the current prompt.

## Required Behavior

### 1. Normal Free Talk Behavior When No Context Exists

When the user is in **Free Talk mode** and no contextual materials are detected:
- transcribe the voice normally,
- send the voice transcript to the agent normally,
- do not force the user to press send,
- do not hold the prompt,
- do not create unnecessary multimodal bundles,
- do not delay the conversation.

This should feel like regular hands-free voice chat.

### 2. Context-Aware Holding Behavior

When context is detected, Free Talk mode should temporarily switch into a “context bundle pending” behavior.

In this state:
- voice transcript should be held in the draft composer,
- files/images/screenshots/links should remain attached to the same draft,
- the user should be able to keep adding context,
- nothing should be sent to the agent until the bundle is complete,
- the user can manually press “Send as one prompt,”
- or the system can send only when a clear send intent is detected, such as “send it now.”

The agent should receive one unified prompt containing:
- the voice transcript,
- typed text,
- images,
- screenshots,
- files,
- links,
- captions or notes,
- context ordering,
- and the final user instruction.

### 3. Context Detection Logic

Implement a context detector that checks for:

#### Explicit attachments
- image upload,
- screenshot upload,
- file upload,
- pasted link,
- link preview,
- selected canvas item,
- current multimodal draft state.

#### Voice/text references
Detect phrases such as:
- “this screenshot”
- “this image”
- “the picture”
- “the highlighted part”
- “the file”
- “the document”
- “this link”
- “the attachment”
- “what I uploaded”
- “look at this”
- “read this”
- “don’t read this out loud”
- “use this with what I’m saying”

#### Active composer state
The system should treat context as active if:
- an upload is in progress,
- an attachment card is present,
- a link card is present,
- the user recently attached context,
- the user is editing a multimodal draft,
- or the user has not yet sent/canceled the context bundle.

### 4. Context Timeout / Reset

Avoid getting stuck in context-hold mode.

Add logic so the context-bundle state clears when:
- the user sends the bundle,
- the user cancels the bundle,
- all attachments are removed,
- the user explicitly says “cancel that,” “ignore the screenshot,” or “never mind,”
- or the context draft expires after a reasonable period of inactivity.

When the context state clears, Free Talk should return to normal automatic sending.

### 5. Voice Mode Button

Add a mode button next to the existing mic mute button.

The button should cycle through:

1. **Wake Word**
2. **Free Talk**
3. **Push To Talk**

Each mode should have a clear visual state and accessible label.

Example labels:
- “Mode: Wake Word”
- “Mode: Free Talk”
- “Mode: Push To Talk”

The current selected mode should persist across sessions unless the user changes it.

### 6. Wake Word Integration

The project now contains a directory with various wake-word files.

Locate the added wake-word directory and integrate it into the voice pipeline.

Implement:
- loading available wake-word models/files from that directory,
- selecting the active wake word,
- starting passive wake-word listening when in Wake Word mode,
- activating recording only after the wake word is detected,
- avoiding accidental sends before wake-word activation,
- showing a UI indication when the system is listening for the wake word,
- showing a UI indication when the wake word has triggered active listening.

If multiple wake words are available, provide a simple configuration point for selecting which one to use.

### 7. Mode-Specific Behavior

#### Wake Word Mode
Behavior:
- microphone can listen passively for the wake word,
- do not send speech to the agent until wake word activation,
- after wake word activation, record/transcribe the user’s request,
- if no extra context is detected, send normally,
- if context is detected, hold and bundle the voice with the context.

#### Free Talk Mode
Behavior:
- voice conversation flows automatically,
- if no context is detected, send transcripts normally,
- if context is detected, hold transcripts and attachments together as one multimodal prompt,
- return to normal after the context bundle is sent or cleared.

#### Push To Talk Mode
Behavior:
- only record while the user is pressing or holding the push-to-talk control,
- after release, transcribe the captured speech,
- if no context is detected, send normally,
- if context is detected, add transcript to the context bundle and wait for send confirmation.

### 8. Unified Prompt Bundle

When context is detected, build a structured bundle like:

```json
{
  "type": "multimodal_user_prompt",
  "voice_mode": "wake_word | free_talk | push_to_talk",
  "send_reason": "manual_send | voice_send_intent | normal_no_context",
  "voice": {
    "transcript": "",
    "audio_file_id": "",
    "started_at": "",
    "ended_at": ""
  },
  "text": {
    "typed_notes": "",
    "final_instruction": ""
  },
  "attachments": [
    {
      "type": "image | screenshot | file | link",
      "id": "",
      "name": "",
      "url": "",
      "caption": "",
      "user_note": ""
    }
  ],
  "context_detection": {
    "has_context": true,
    "signals": []
  }
}
````

### 9. Agent-Facing Prompt Assembly

When the final context bundle is sent, assemble the prompt in a format similar to:

```markdown
## User Voice Transcript
...

## Typed Notes
...

## Attached Context
- Screenshot 1: ...
- Image 1: ...
- File 1: ...
- Link 1: ...

## Final User Request
...

## Agent Instruction
Use the voice transcript and attached context together. Do not answer using only one part. If the user references an image, screenshot, link, file, highlighted area, or attachment, inspect that context before answering.
```

### 10. Safeguards

Add safeguards so:

* transcription completion alone does not send the prompt when context is active,
* upload completion alone does not send the prompt,
* link-preview completion alone does not send the prompt,
* the agent does not receive partial multimodal context,
* Free Talk mode does not get permanently stuck waiting,
* normal no-context speech still works without added friction,
* wake-word mode does not accidentally send background speech,
* push-to-talk mode only records during intentional activation.

### 11. Memory Integration

If the user gives a durable preference through voice or text, write it to memory.

Examples:

* “Always use Wake Word mode.”
* “Default to Free Talk.”
* “Don’t send voice with screenshots until I press send.”
* “When I attach pictures, wait for my explanation.”
* “Call the agent Hermes-Agent.”

Memory should influence future behavior and persona responses.

### 12. Tests to Add

Add or update tests for:

#### Free Talk without context

* voice transcript sends normally,
* no manual send required,
* no multimodal bundle created unnecessarily.

#### Free Talk with context

* screenshot + voice are bundled,
* link + voice are bundled,
* image + voice are bundled,
* transcript completion does not prematurely send,
* upload completion does not prematurely send,
* manual send sends the full bundle.

#### Context reset

* removing all attachments returns Free Talk to normal,
* sending the bundle clears context state,
* canceling the bundle clears context state,
* timeout clears stale context state.

#### Wake Word mode

* wake-word files are detected from the new directory,
* passive listening starts in Wake Word mode,
* recording starts only after wake-word detection,
* no background speech is sent before wake-word activation,
* after wake-word activation, no-context speech sends normally,
* after wake-word activation, contextual speech is bundled.

#### Push To Talk mode

* recording only occurs during push-to-talk activation,
* release triggers transcription,
* no-context speech sends normally,
* contextual speech is bundled.

#### Mode selector

* button cycles through Wake Word, Free Talk, and Push To Talk,
* selected mode persists across sessions,
* UI label updates correctly,
* mute button remains independent from mode selection.

### 13. Deliverables

Make the code changes directly.

Then provide:

* what files changed,
* how Free Talk no-context behavior works,
* how context detection works,
* how context bundling works,
* how wake-word integration works,
* how the mode button works,
* what memory behavior was added or changed,
* what tests were added,
* test results,
* any remaining risks or follow-up work.

Prioritize natural voice behavior: normal speech should remain fast and automatic, while voice plus screenshots/files/links/images should become one shared-context prompt.

```
```
