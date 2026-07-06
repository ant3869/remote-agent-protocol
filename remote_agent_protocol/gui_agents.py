"""Agents panel -- the delegated-tasks window (Toplevel owned by the main GUI).

Split out of gui.py to keep files under control. Owns job bookkeeping for
display; the actual jobs live in the session's AgentBridge. All calls happen
on the Tk thread (events arrive via the main GUI's pump).
"""

from tkinter import BOTH, END, LEFT, Frame, Label, StringVar, Toplevel, X, ttk

from remote_agent_protocol import agent_bridge
from remote_agent_protocol import gui_theme as theme

_TERMINAL = ("done", "failed", "cancelled")
_ACTIVE = ("running", "waiting", "blocked")
_GLYPHS = {
    "running": "●",
    "waiting": "◐",
    "blocked": "!",
    "done": "✓",
    "failed": "✕",
    "cancelled": "–",
}
_ROW_COLORS = {
    "running": theme.FG,
    "waiting": theme.WARN,
    "blocked": theme.DANGER,
    "done": theme.SUBTLE,
    "failed": theme.DANGER,
    "cancelled": theme.DIM,
}


class AgentsPanel:
    """Task delegation window: start, watch, cancel, copy, clean up."""

    def __init__(self, root, session, append_sys) -> None:
        """Initialize the panel.

        Args:
            root: The main Tk window that owns this Toplevel.
            session: The VoiceSession jobs are started/cancelled through.
            append_sys: Writes a dim status line into the main transcript.
        """
        self._root = root
        self._session = session
        self._append_sys = append_sys
        self._window: Toplevel | None = None
        self._jobs: dict[str, dict] = {}  # job_id -> {agent, task, status, lines, secs}
        self._order: list[str] = []
        self._history_loaded = False

    # -- queries used by the main window -------------------------------------

    def rebind_session(self, session) -> None:
        """Point the panel at a freshly rebuilt VoiceSession."""
        self._session = session

    def running_count(self) -> int:
        """Number of jobs currently running (for the topbar badge)."""
        return sum(1 for j in self._jobs.values() if j.get("status") in _ACTIVE)

    def active_summary(self) -> str:
        """Compact current-agent activity for the main-window badge."""
        for job_id in reversed(self._order):
            job = self._jobs[job_id]
            if job.get("status") not in _ACTIVE:
                continue
            agent = job.get("agent", "agent").replace("-", " ").title().replace(" ", "")
            state = job.get("state", job.get("status", "running")).replace("_", " ").upper()
            action = job.get("action") or job.get("task", "")
            return f"{agent} · {state} · {action}"[:58]
        return ""

    def recent_jobs(self) -> list[dict]:
        """Job rows (newest last) for the diagnostics bundle."""
        return [dict(self._jobs[job_id], job_id=job_id) for job_id in self._order]

    def _load_history_once(self) -> None:
        """Seed the panel with jobs persisted from previous runs, once."""
        if self._history_loaded:
            return
        self._history_loaded = True
        for index, row in enumerate(self._session.agent_history()):
            # Index-prefixed: raw job ids restart at job-1 every session, so
            # rows from different runs would otherwise collide and drop.
            job_id = f"hist-{index}-{row.get('job_id', '')}"
            if job_id in self._jobs:
                continue
            self._jobs[job_id] = {
                "agent": row.get("agent", "?"),
                "machine": row.get("machine", "local"),
                "task": row.get("task", ""),
                "status": row.get("status", "?"),
                "secs": row.get("secs"),
                "lines": list(row.get("lines", [])),
                **{
                    key: row.get(key)
                    for key in (
                        "state",
                        "action",
                        "tool",
                        "step",
                        "step_total",
                        "last_completed_step",
                        "summary",
                        "result",
                    )
                },
            }
            self._order.append(job_id)

    # -- window ---------------------------------------------------------------

    def open(self) -> None:
        """Show the agents window, creating it (and loading history) on first use."""
        if self._window is not None and self._window.winfo_exists():
            self._window.lift()
            return

        self._load_history_once()
        win = Toplevel(self._root)
        self._window = win
        win.title("Agent tasks")
        win.geometry(f"900x600+{self._root.winfo_rootx() + 70}+{self._root.winfo_rooty() + 70}")
        win.minsize(760, 480)
        win.configure(bg=theme.BG)
        theme.enable_dark_title_bar(win)
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_window", None), win.destroy()))

        header = Frame(win, bg=theme.BG, padx=20, pady=14)
        header.pack(fill=X)
        Label(header, text="Agent tasks", bg=theme.BG, fg=theme.FG, font=theme.FONT_TITLE).pack(
            anchor="w"
        )
        Label(
            header,
            text="Delegate work to a coding agent and watch the output stream back.",
            bg=theme.BG,
            fg=theme.SUBTLE,
            font=theme.FONT_SMALL,
        ).pack(anchor="w")

        form = theme.card(win, pad=12)
        form.pack(fill=X, padx=20)
        row = Frame(form, bg=theme.CARD)
        row.pack(fill=X)
        Label(row, text="AGENT", bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SECTION).pack(
            side=LEFT, padx=(0, 6)
        )
        backends = self._session.agent_backends()
        self.agent_box = ttk.Combobox(row, values=backends, state="readonly", width=14)
        if backends:
            self.agent_box.set(backends[0])
        self.agent_box.pack(side=LEFT, padx=(0, 8))
        self.agent_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_target())
        self.target_label = Label(
            row, text="", bg=theme.CARD, fg=theme.DIM, font=theme.FONT_MONO_SMALL
        )
        self.target_label.pack(side=LEFT, padx=(0, 10))
        self.task_var = StringVar()
        task_entry = theme.entry(
            row, textvariable=self.task_var, placeholder="Describe the task…", font=theme.FONT
        )
        task_entry.pack(side=LEFT, fill=X, expand=True, ipady=5, padx=(0, 10))
        task_entry.bind("<Return>", lambda _e: self._start_task())
        self._task_entry = task_entry
        theme.button(row, "Start task", self._start_task, kind="primary").pack(side=LEFT)

        wd = Frame(form, bg=theme.CARD)
        wd.pack(fill=X, pady=(8, 0))
        Label(wd, text="WORKING DIR", bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SECTION).pack(
            side=LEFT, padx=(0, 8)
        )
        self.workdir_var = StringVar()
        self._workdir_entry = theme.entry(
            wd,
            textvariable=self.workdir_var,
            placeholder="Blank = agent default (used for repo/code tasks)",
            font=theme.FONT,
        )
        self._workdir_entry.pack(side=LEFT, fill=X, expand=True, ipady=4)

        panes = Frame(win, bg=theme.BG, padx=20, pady=14)
        panes.pack(fill=BOTH, expand=True)

        left = Frame(panes, bg=theme.BG, width=330)
        left.pack(side=LEFT, fill="y", padx=(0, theme.GAP))
        left.pack_propagate(False)
        self._jobs_header = Label(left, text="Jobs", bg=theme.BG, fg=theme.FG, font=theme.FONT_H2)
        self._jobs_header.pack(anchor="w", pady=(0, 6))
        self.job_list = theme.listbox(left)
        self.job_list.pack(fill=BOTH, expand=True)
        self.job_list.bind("<<ListboxSelect>>", lambda _e: self._show_log())
        btns = Frame(left, bg=theme.BG)
        btns.pack(fill=X, pady=(theme.GAP_SM, 0))
        theme.button(btns, "Cancel", self._cancel_selected, kind="danger").pack(side=LEFT)
        theme.button(btns, "Clear finished", self._clear_finished, kind="ghost").pack(
            side=LEFT, padx=(theme.GAP_SM, 0)
        )

        right = Frame(panes, bg=theme.BG)
        right.pack(side=LEFT, fill=BOTH, expand=True)
        Label(right, text="Live output", bg=theme.BG, fg=theme.FG, font=theme.FONT_H2).pack(
            anchor="w", pady=(0, 6)
        )
        self.log = theme.scrolled_text(right, font=theme.FONT_MONO_SMALL)
        self.log.frame.pack(fill=BOTH, expand=True)
        self.log.tag_config("dim", foreground=theme.DIM)
        self.log.configure(state="disabled")
        rbtns = Frame(right, bg=theme.BG)
        rbtns.pack(fill=X, pady=(theme.GAP_SM, 0))
        theme.button(rbtns, "Copy output", self._copy_output).pack(side=LEFT)
        theme.button(rbtns, "Speak result", self._speak_result).pack(
            side=LEFT, padx=(theme.GAP_SM, 0)
        )

        self._set_log_placeholder()
        self._refresh_target()
        self._refresh_list()

    # -- actions ----------------------------------------------------------------

    def _start_task(self) -> None:
        agent = self.agent_box.get()
        if theme.placeholder_active(self._task_entry):
            return
        task = self.task_var.get().strip()
        if not agent or not task:
            return
        cwd = None
        if not theme.placeholder_active(self._workdir_entry):
            cwd = self.workdir_var.get().strip() or None
        self.task_var.set("")
        self._session.start_agent_task(agent, task, cwd)

    def _refresh_target(self) -> None:
        agent = self.agent_box.get()
        machine = self._session.agent_machine(agent) if agent else ""
        self.target_label.configure(text=f"@ {machine}" if machine else "")

    def _selected_job_id(self) -> str | None:
        if self._window is None or not self._window.winfo_exists():
            return None
        selection = self.job_list.curselection()
        if not selection or selection[0] >= len(self._order):
            return None
        return self._order[selection[0]]

    def _cancel_selected(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self._session.cancel_agent_task(job_id)

    def _clear_finished(self) -> None:
        keep = [j for j in self._order if self._jobs[j].get("status") not in _TERMINAL]
        for job_id in self._order:
            if job_id not in keep:
                self._jobs.pop(job_id, None)
        self._order = keep
        self._refresh_list()

    def _copy_output(self) -> None:
        job_id = self._selected_job_id()
        if job_id is None:
            return
        self._root.clipboard_clear()
        self._root.clipboard_append("\n".join(self._jobs[job_id]["lines"]))

    def _speak_result(self) -> None:
        """Have Jess speak the selected job's result again, out loud."""
        job_id = self._selected_job_id()
        if job_id is None:
            return
        job = self._jobs[job_id]
        spoken = job.get("result") or job.get("summary") or agent_bridge.summarize_output(job["lines"])
        if spoken:
            self._session.announce_text(
                f"Result of task '{job['task']}' on agent '{job['agent']}': {spoken}"
            )

    # -- event handling (called from the main GUI pump) --------------------------

    def handle_event(self, evt: dict) -> None:
        """Fold one agent_job event into the panel state and re-render."""
        job_id = evt.get("job_id", "")
        job = self._jobs.setdefault(
            job_id,
            {
                "agent": evt.get("agent", "?"),
                "machine": evt.get("machine", "local"),
                "task": evt.get("task", ""),
                "lines": [],
            },
        )
        job["status"] = evt.get("status", "?")
        for field_name in (
            "state",
            "action",
            "tool",
            "step",
            "step_total",
            "last_completed_step",
            "summary",
            "result",
            "elapsed_secs",
        ):
            if field_name in evt:
                job[field_name] = evt[field_name]
        if "secs" in evt:
            job["secs"] = evt["secs"]
        if job_id not in self._order:
            self._order.append(job_id)

        event = evt.get("event")
        if event == "started":
            self._append_sys(f"Agent task started [{job['agent']}]: {job['task']}")
        elif event == "output":
            job["lines"].append(evt.get("line", ""))
            if self._selected_job_id() == job_id:
                self._append_log(evt.get("line", ""))
        elif event == "finished":
            took = f" in {job['secs']}s" if job.get("secs") is not None else ""
            self._append_sys(f"Agent task {job['status']}{took} [{job['agent']}]: {job['task']}")
        elif event == "progress" and evt.get("importance") in {"milestone", "attention"}:
            self._append_sys(f"{job['agent']}: {job.get('action', job.get('state', 'working'))}")
        self._refresh_list()
        # Keep the detail pane live for the job in focus. Without this the status
        # block freezes on its first render and a long-running job looks stuck,
        # even as progress/finished events keep arriving. "output" already
        # streams incrementally above, so only the status-only events re-render.
        if event in {"started", "progress", "finished"} and self._selected_job_id() == job_id:
            self._show_log()

    # -- rendering ---------------------------------------------------------------

    def _line_for(self, job: dict) -> str:
        status = job.get("status", "?")
        glyph = _GLYPHS.get(status, "…")
        took = f" ({job['secs']}s)" if job.get("secs") is not None else ""
        target = f"{job['machine']}/{job['agent']}"
        state = job.get("state", status).replace("_", " ").upper()
        action = job.get("action") or job.get("task", "")
        tool = f" · {job['tool']}" if job.get("tool") else ""
        step = ""
        if job.get("step"):
            step = f" · {job['step']}/{job.get('step_total') or '?'}"
        return f" {glyph}  {target} · {state}{tool}{step} · {action[:42]}{took}"

    def _refresh_list(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            return
        selected = self._selected_job_id()
        self.job_list.delete(0, END)
        for index, job_id in enumerate(self._order):
            job = self._jobs[job_id]
            self.job_list.insert(END, self._line_for(job))
            self.job_list.itemconfig(index, fg=_ROW_COLORS.get(job.get("status", ""), theme.FG))
        if not self._order:
            theme.set_list_placeholder(self.job_list, "No jobs yet — start a task above.")
        if selected in self._order:
            self.job_list.selection_set(self._order.index(selected))
        self._jobs_header.configure(text=f"Jobs · {len(self._order)}" if self._order else "Jobs")

    def _set_log_placeholder(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", END)
        self.log.insert(END, "Select a job to see its output.\n", "dim")
        self.log.configure(state="disabled")

    def _show_log(self) -> None:
        job_id = self._selected_job_id()
        if job_id is None:
            return
        self.log.configure(state="normal")
        self.log.delete("1.0", END)
        job = self._jobs[job_id]
        details = [
            f"Status: {job.get('state', job.get('status', '?'))}",
            f"Current action: {job.get('action') or '—'}",
            f"Active tool: {job.get('tool') or '—'}",
            f"Step: {job.get('step') or '—'} / {job.get('step_total') or '—'}",
            f"Last completed: {job.get('last_completed_step') or '—'}",
            "",
        ]
        body = list(job["lines"])
        # Agents that report only via status markers stream no raw output, so on
        # completion surface the summary/result -- otherwise the pane just shows
        # a frozen status block with no answer.
        if job.get("status") in _TERMINAL:
            summary = job.get("summary")
            result = job.get("result")
            if summary:
                body.append(f"— {summary}")
            if result and result != summary:
                body.append("")
                body.append("Result:")
                body.append(result)
        self.log.insert(END, "\n".join(details + body) + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        if self._window is None or not self._window.winfo_exists():
            return
        self.log.configure(state="normal")
        self.log.insert(END, line + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")
