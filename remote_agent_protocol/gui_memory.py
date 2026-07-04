"""Memory panel -- the memory-manager window (Toplevel owned by the main GUI).

Split out of gui.py for file-size sanity. Pure view/controller: all memory
access goes through the session's thread-safe surface; rows arrive back as
events routed here by the main GUI pump.
"""

from tkinter import BOTH, END, LEFT, Frame, Label, StringVar, Toplevel, X, messagebox

from remote_agent_protocol import gui_theme as theme
from remote_agent_protocol import memory_manager


class MemoryPanel:
    """Short-term transcript + long-term semantic memory, with controls."""

    def __init__(self, root, session, append_sys) -> None:
        """Initialize the panel.

        Args:
            root: The main Tk window that owns this Toplevel.
            session: The VoiceSession memory calls go through.
            append_sys: Writes a dim status line into the main transcript.
        """
        self._root = root
        self._session = session
        self._append_sys = append_sys
        self._window: Toplevel | None = None
        self._semantic_rows: list[dict] = []

    def rebind_session(self, session) -> None:
        """Point the panel at a freshly rebuilt VoiceSession."""
        self._session = session

    def open(self) -> None:
        """Show the memory window and request fresh rows from the session."""
        if self._window is not None and self._window.winfo_exists():
            self._window.lift()
            self._session.refresh_memories()
            return

        win = Toplevel(self._root)
        self._window = win
        win.title("Memory")
        win.geometry(f"820x560+{self._root.winfo_rootx() + 90}+{self._root.winfo_rooty() + 90}")
        win.minsize(680, 440)
        win.configure(bg=theme.BG)
        theme.enable_dark_title_bar(win)
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_window", None), win.destroy()))

        header = Frame(win, bg=theme.BG, padx=20, pady=14)
        header.pack(fill=X)
        Label(header, text="Memory", bg=theme.BG, fg=theme.FG, font=theme.FONT_TITLE).pack(
            anchor="w"
        )
        Label(
            header,
            text="What Jess keeps in mind — the rolling transcript and pinned long-term facts.",
            bg=theme.BG,
            fg=theme.SUBTLE,
            font=theme.FONT_SMALL,
        ).pack(anchor="w")

        toolbar = theme.card(win, pad=12)
        toolbar.pack(fill=X, padx=20)
        search_row = Frame(toolbar, bg=theme.CARD)
        search_row.pack(fill=X)
        self.query_var = StringVar()
        search_entry = theme.entry(
            search_row,
            textvariable=self.query_var,
            placeholder="Search long-term memories…",
            font=theme.FONT,
        )
        search_entry.pack(side=LEFT, fill=X, expand=True, ipady=5, padx=(0, 10))
        search_entry.bind("<Return>", lambda _e: self._search())
        self._search_entry = search_entry
        theme.button(search_row, "Search", self._search).pack(side=LEFT, padx=(0, 6))
        theme.button(
            search_row, "Refresh", lambda: self._session.refresh_memories(), kind="ghost"
        ).pack(side=LEFT)

        remember_row = Frame(toolbar, bg=theme.CARD)
        remember_row.pack(fill=X, pady=(8, 0))
        self.manual_var = StringVar()
        manual_entry = theme.entry(
            remember_row,
            textvariable=self.manual_var,
            placeholder="Pin a fact Jess should remember…",
            font=theme.FONT,
        )
        manual_entry.pack(side=LEFT, fill=X, expand=True, ipady=5, padx=(0, 10))
        manual_entry.bind("<Return>", lambda _e: self._remember())
        self._manual_entry = manual_entry
        theme.button(remember_row, "Remember", self._remember, kind="primary").pack(side=LEFT)

        panes = Frame(win, bg=theme.BG, padx=20, pady=14)
        panes.pack(fill=BOTH, expand=True)

        left = Frame(panes, bg=theme.BG)
        left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, theme.GAP))
        self._short_header = Label(
            left, text="Short-term transcript", bg=theme.BG, fg=theme.FG, font=theme.FONT_H2
        )
        self._short_header.pack(anchor="w", pady=(0, 6))
        self.short_list = theme.listbox(left, font=theme.FONT_SMALL)
        self.short_list.pack(fill=BOTH, expand=True)
        theme.button(left, "Forget short-term", self._forget_short, kind="danger").pack(
            fill=X, pady=(theme.GAP_SM, 0)
        )

        right = Frame(panes, bg=theme.BG)
        right.pack(side=LEFT, fill=BOTH, expand=True)
        self._semantic_header = Label(
            right, text="Long-term memories", bg=theme.BG, fg=theme.FG, font=theme.FONT_H2
        )
        self._semantic_header.pack(anchor="w", pady=(0, 6))
        self.semantic_list = theme.listbox(right, font=theme.FONT_SMALL)
        self.semantic_list.pack(fill=BOTH, expand=True)
        btns = Frame(right, bg=theme.BG)
        btns.pack(fill=X, pady=(theme.GAP_SM, 0))
        theme.button(btns, "Delete selected", self._delete_selected).pack(side=LEFT)
        theme.button(btns, "Forget all", self._forget_semantic, kind="danger").pack(
            side=LEFT, padx=(theme.GAP_SM, 0)
        )

        theme.set_list_placeholder(self.short_list, "Loading…")
        theme.set_list_placeholder(self.semantic_list, "Loading…")
        self._session.refresh_memories()

    # -- actions ---------------------------------------------------------------

    def _search(self) -> None:
        query = "" if theme.placeholder_active(self._search_entry) else self.query_var.get()
        self._session.refresh_memories(query)

    def _remember(self) -> None:
        if theme.placeholder_active(self._manual_entry):
            return
        text = self.manual_var.get().strip()
        if not text:
            return
        self._session.add_semantic_memory(text)
        self.manual_var.set("")
        self._append_sys("Pinned semantic memory.")

    def _forget_short(self) -> None:
        if not messagebox.askyesno(
            "Forget short-term memory?", "Clear saved transcript memory and live context?"
        ):
            return
        self._append_sys("Short-term memory cleared.")
        self._session.forget_short_term_memory()

    def _forget_semantic(self) -> None:
        if not messagebox.askyesno(
            "Forget semantic memory?", "Delete all long-term semantic memories for Ant?"
        ):
            return
        self._append_sys("Semantic memories cleared.")
        self._session.forget_semantic_memory()

    def _delete_selected(self) -> None:
        if self._window is None:
            return
        selection = self.semantic_list.curselection()
        if not selection or selection[0] >= len(self._semantic_rows):
            return
        row = self._semantic_rows[selection[0]]
        memory_id = row.get("id", "")
        if memory_id:
            self._append_sys(f"Deleted memory {memory_id[:8]}.")
            self._session.delete_semantic_memory(memory_id)

    # -- event handling ----------------------------------------------------------

    def handle_event(self, evt: dict) -> None:
        """Render a memory-rows event into the matching list widget."""
        if self._window is None or not self._window.winfo_exists():
            return
        scope = evt.get("scope")
        rows = evt.get("rows", [])
        if scope == "short":
            self.short_list.delete(0, END)
            for row in rows:
                self.short_list.insert(END, row)
            if not rows:
                theme.set_list_placeholder(self.short_list, "No transcript yet — start talking.")
            self._short_header.configure(
                text=f"Short-term transcript · {len(rows)}" if rows else "Short-term transcript"
            )
        elif scope == "semantic":
            self._semantic_rows = rows
            self.semantic_list.delete(0, END)
            for row in rows:
                self.semantic_list.insert(END, memory_manager.display_line(row))
            if not rows:
                theme.set_list_placeholder(self.semantic_list, "No memories yet — pin one above.")
            self._semantic_header.configure(
                text=f"Long-term memories · {len(rows)}" if rows else "Long-term memories"
            )
