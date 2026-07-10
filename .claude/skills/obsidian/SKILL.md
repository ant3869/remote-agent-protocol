---
name: obsidian
description: Integration with an Obsidian vault for managing notes, tasks, and knowledge. Supports adding notes, creating tasks, and organizing project documentation using MOCs, properties, and practical organization patterns. Use when the user asks to add/update a note, create a task, or organize content in their Obsidian vault.
---

# Obsidian

## Instructions

### Step 1: Respect the vault's existing structure

Before adding a note, look at how similar notes are already organized in the
vault (folder layout, naming pattern, existing tags/properties). Match it
rather than introducing a new convention — Obsidian's value comes from
consistent linking and search, which breaks down if every note follows a
different pattern.

### Step 2: Notes and linking

- Use `[[wikilinks]]` to connect related notes rather than duplicating
  content across them.
- Prefer many small, well-linked notes over one large note trying to cover
  everything — Obsidian's graph and backlinks work best with atomic notes.
- Add YAML frontmatter properties (`tags:`, `status:`, `created:`, etc.) that
  match the schema already used elsewhere in the vault, so search/Dataview
  queries keep working.

### Step 3: Maps of Content (MOCs)

For a topic with many related notes, create or update a MOC — a note that
exists mainly as a curated list of links into the topic, organized by
subtopic. Add new notes to the relevant MOC rather than leaving them
orphaned with no inbound links.

### Step 4: Tasks

Use Obsidian's native task syntax so tasks are queryable by the Tasks plugin
if the vault has it:

```markdown
- [ ] Task description #tag [due:: 2026-07-15]
```

Place tasks in the note they're contextually tied to (a project note, a
meeting note) rather than a single undifferentiated task dump, unless the
vault already uses a centralized task list convention.

### Step 5: Obsidian CLI

If the `obsidian` CLI (1.12+) is available, prefer it for scripted vault
operations (creating notes, querying, moving files) over manually
constructing file paths — it handles vault-relative paths and existing
frontmatter correctly. Fall back to direct file read/write only when the CLI
doesn't cover the operation needed.

### Step 6: Don't break existing links

When renaming or moving a note, update or verify any `[[wikilinks]]`
pointing to it — Obsidian auto-updates links on rename only when done through
Obsidian itself, not through an external file move.
