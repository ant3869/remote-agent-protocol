---
name: hackmd
description: HackMD collaborative markdown - slide presentations, embedded SVG diagrams, and real-time editing best practices. Use when the user asks to create or edit a HackMD document, build slides in HackMD, or embed a diagram into a HackMD note.
---

# HackMD

## Instructions

### Step 1: Standard notes

Write plain GitHub-flavored markdown. HackMD supports the usual headings,
tables, code fences (with syntax highlighting), task lists, and footnotes.
Keep documents structured with clear headings so HackMD's built-in
table-of-contents/outline stays useful.

### Step 2: Slide presentations

HackMD turns a note into slides when it starts with front matter setting
`type: slide`:

```markdown
---
type: slide
slideOptions:
  theme: white
  transition: slide
---

# Title Slide

---

## Second Slide

Content here
```

Use `---` on its own line to separate horizontal slides and `--` for vertical
sub-slides (reveal.js semantics). Keep one idea per slide; HackMD renders
markdown as-is with no auto-summarization, so overly long slides just
overflow.

### Step 3: Embedded SVG diagrams

Inline SVG works directly in HackMD markdown:

```markdown
<svg width="200" height="100">
  <rect width="200" height="100" style="fill:rgb(0,0,255);" />
</svg>
```

For diagrams-as-code, HackMD also renders Mermaid fenced blocks
(```mermaid ... ```) — prefer Mermaid over hand-written SVG when the diagram
is a flowchart/sequence/graph, since it stays editable as text; use raw SVG
only for custom shapes Mermaid can't express.

### Step 4: Real-time editing etiquette

- HackMD documents are collaboratively edited live — avoid large
  wholesale rewrites of a document others may be actively viewing/editing;
  prefer targeted section edits.
- Use HackMD's built-in version history instead of keeping manual "backup"
  copies of a note.
- When sharing a HackMD link, note whether it's read-only or edit-permission,
  since that determines whether the recipient can collaborate live or just
  view.
