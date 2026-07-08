"""Memory panel -- the memory-manager window (Toplevel owned by the main GUI).

Split out of gui.py for file-size sanity. Pure view/controller: all memory
access goes through the session's thread-safe surface; rows arrive back as
events routed here by the main GUI pump.
"""

from tkinter import BOTH, END, LEFT, RIGHT, Frame, Label, StringVar, Toplevel, X, messagebox

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
        win.geometry(f"1120x700+{self._root.winfo_rootx() + 90}+{self._root.winfo_rooty() + 70}")
        win.minsize(900, 560)
        win.configure(bg=theme.BG)
        theme.enable_dark_title_bar(win)
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_window", None), win.destroy()))

        header = Frame(win, bg=theme.BG, padx=24, pady=18)
        header.pack(fill=X)
        title = Frame(header, bg=theme.BG)
        title.pack(side=LEFT)
        Label(title, text="Memory", bg=theme.BG, fg=theme.FG, font=("Segoe UI Semibold", 20)).pack(
            anchor="w"
        )
        Label(
            title,
            text="Search, pin, and inspect what Jess keeps in context.",
            bg=theme.BG,
            fg=theme.SUBTLE,
            font=theme.FONT_SMALL,
        ).pack(anchor="w")

        stats = Frame(header, bg=theme.BG)
        stats.pack(side=RIGHT)
        self.short_stat = theme.StatusPill(stats, "Diary")
        self.short_stat.pack(side=LEFT, padx=(0, theme.GAP_SM))
        self.semantic_stat = theme.StatusPill(stats, "Nodes")
        self.semantic_stat.pack(side=LEFT)
        self.short_stat.set("0", "neutral")
        self.semantic_stat.set("0", "neutral")

        toolbar_panel = theme.panel(
            win, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=14, radius=22
        )
        toolbar_panel.pack(fill=X, padx=24)
        toolbar = toolbar_panel.body
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
        theme.button(search_row, "Search", self._search, kind="primary").pack(
            side=LEFT, padx=(0, 6)
        )
        theme.button(
            search_row, "Refresh", lambda: self._session.refresh_memories(), kind="ghost"
        ).pack(side=LEFT)

        tabs = Frame(toolbar, bg=theme.CARD)
        tabs.pack(fill=X, pady=(theme.GAP, 0))
        for text, command, kind in (
            ("Diary", lambda: self.short_list.focus_set(), "ghost"),
            ("Knowledge", lambda: self.semantic_list.focus_set(), "selected"),
            ("Pinned facts", lambda: self._manual_entry.focus_set(), "ghost"),
        ):
            theme.button(tabs, text, command, kind=kind).pack(side=LEFT, padx=(0, theme.GAP_SM))

        remember_row = Frame(toolbar, bg=theme.CARD)
        remember_row.pack(fill=X, pady=(theme.GAP, 0))
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

        panes = Frame(win, bg=theme.BG, padx=24, pady=18)
        panes.pack(fill=BOTH, expand=True)

        left_panel = theme.panel(panes, bg=theme.CARD, border=theme.BORDER, pad=0, radius=22)
        left_panel.pack(side=LEFT, fill=BOTH, padx=(0, theme.GAP))
        left_panel.configure(width=260)
        left_panel.pack_propagate(False)
        left = left_panel.body
        left.configure(padx=0, pady=0)
        nav_header = Frame(left, bg=theme.CARD, padx=14, pady=10)
        nav_header.pack(fill=X)
        self._short_header = Label(
            nav_header, text="Memory tree", bg=theme.CARD, fg=theme.FG, font=theme.FONT_H2
        )
        self._short_header.pack(side=LEFT)
        self.short_list = theme.listbox(left, font=theme.FONT_SMALL)
        self.short_list.pack(fill=BOTH, expand=True, padx=1, pady=(0, 1))
        theme.button(left, "Forget short-term", self._forget_short, kind="danger").pack(
            fill=X, padx=12, pady=(theme.GAP_SM, 12)
        )

        center_panel = theme.panel(
            panes, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=0, radius=22
        )
        center_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, theme.GAP))
        center = center_panel.body
        center.configure(padx=0, pady=0)
        center_header = Frame(center, bg=theme.CARD, padx=14, pady=10)
        center_header.pack(fill=X)
        self._semantic_header = Label(
            center_header, text="Knowledge graph", bg=theme.CARD, fg=theme.FG, font=theme.FONT_H2
        )
        self._semantic_header.pack(side=LEFT)
        Label(
            center_header,
            text="select a node for details",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(side=RIGHT)
        self.semantic_list = theme.listbox(center, font=theme.FONT_SMALL)
        self.semantic_list.pack(fill=BOTH, expand=True, padx=1)
        self.semantic_list.bind("<<ListboxSelect>>", lambda _e: self._show_selected_memory())
        btns = Frame(center, bg=theme.CARD, padx=12, pady=12)
        btns.pack(fill=X)
        theme.button(btns, "Delete selected", self._delete_selected).pack(side=LEFT)
        theme.button(btns, "Forget all", self._forget_semantic, kind="danger").pack(
            side=LEFT, padx=(theme.GAP_SM, 0)
        )

        detail_panel = theme.panel(panes, bg=theme.CARD, border=theme.BORDER, pad=0, radius=22)
        detail_panel.pack(side=LEFT, fill=BOTH)
        detail_panel.configure(width=300)
        detail_panel.pack_propagate(False)
        detail = detail_panel.body
        detail.configure(padx=0, pady=0)
        detail_header = Frame(detail, bg=theme.CARD, padx=14, pady=10)
        detail_header.pack(fill=X)
        Label(detail_header, text="Details", bg=theme.CARD, fg=theme.FG, font=theme.FONT_H2).pack(
            side=LEFT
        )
        self.detail_text = theme.scrolled_text(detail, font=theme.FONT_MONO_SMALL, padx=12, pady=10)
        self.detail_text.frame.pack(fill=BOTH, expand=True, padx=1, pady=(0, 1))
        self.detail_text.configure(state="disabled")
        self._set_detail("Select a memory node to inspect its raw data.")

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

    def _set_detail(self, text: str) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", END)
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")

    def _show_selected_memory(self) -> None:
        selection = self.semantic_list.curselection()
        if not selection or selection[0] >= len(self._semantic_rows):
            self._set_detail("Select a memory node to inspect its raw data.")
            return
        row = self._semantic_rows[selection[0]]
        fields = [
            ("id", row.get("id") or "—"),
            ("score", row.get("score") if row.get("score") is not None else "—"),
            ("memory", row.get("memory") or row.get("text") or "—"),
        ]
        extra = {k: v for k, v in row.items() if k not in {"id", "score", "memory", "text"}}
        for key, value in extra.items():
            fields.append((key, value))
        self._set_detail("\n\n".join(f"{key.upper()}\n{value}" for key, value in fields))

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
                text=f"Memory tree · {len(rows)}" if rows else "Memory tree"
            )
            self.short_stat.set(str(len(rows)), "ok" if rows else "neutral")
        elif scope == "semantic":
            self._semantic_rows = rows
            self.semantic_list.delete(0, END)
            for row in rows:
                self.semantic_list.insert(END, memory_manager.display_line(row))
            if not rows:
                theme.set_list_placeholder(self.semantic_list, "No memories yet — pin one above.")
            self._semantic_header.configure(
                text=f"Knowledge graph · {len(rows)}" if rows else "Knowledge graph"
            )
            self.semantic_stat.set(str(len(rows)), "accent" if rows else "neutral")
            self._show_selected_memory()
