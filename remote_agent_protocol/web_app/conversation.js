/* Attributable, keyed conversation rendering. No agent text is interpreted as HTML. */
(function (root) {
  "use strict";
  const terminal = new Set(["done", "failed", "timeout", "cancelled"]);
  const deliveryLabels = {
    text_only: "Text only", queued: "Queued", playing: "Speaking", played: "Spoken",
    interrupted: "Interrupted", failed: "Speech failed", playback_unconfirmed: "Playback unconfirmed",
  };
  const statusLabels = {
    running: "Working", waiting: "Waiting", blocked: "Blocked", done: "Completed",
    failed: "Failed", timeout: "Timed out", cancelled: "Cancelled",
  };

  function createStore() {
    const rows = new Map();
    let epoch = null;
    let truncated = false;
    return {
      rows, get epoch() { return epoch; }, get truncated() { return truncated; },
      clear(nextEpoch = null) { rows.clear(); epoch = nextEpoch; truncated = false; },
      restore(snapshot) {
        rows.clear(); epoch = snapshot.epoch; truncated = Boolean(snapshot.truncated);
        for (const row of snapshot.rows || []) rows.set(row.key, row);
      },
      apply(row) {
        if (!row?.key) return false;
        const old = rows.get(row.key);
        if (old && (row.id || 0) <= (old.id || 0)) return false;
        if (old?.type === "agent_job" && terminal.has(old.status) && !terminal.has(row.status)) return false;
        if (old?.type === "transcript" && old.final && row.final === false) return false;
        rows.set(row.key, { ...old, ...row });
        while (rows.size > 1200) { rows.delete(rows.keys().next().value); truncated = true; }
        return true;
      },
    };
  }

  function outcome(row) {
    const status = statusLabels[row.status] || row.state || "Working";
    const result = row.result || row.summary || "";
    if (row.status === "done" && !result) return { status: "Completed without an answer", text: "The agent finished but returned no answer." };
    if (["failed", "timeout", "cancelled"].includes(row.status)) {
      return { status, text: row.failure_detail || result || `${status}. No additional details were reported.` };
    }
    return { status, text: result };
  }

  function progress(row) {
    const entries = row.activity || [];
    const action = row.action || entries.at(-1)?.text || row.state || "Waiting for agent output.";
    // Keep the latest explanatory update visible when a tool or heartbeat arrives.
    const thought = [...entries].reverse().find(entry => entry.text && entry.text !== action
      && !/^(Running\b|Still working\b|Starting\b)/i.test(entry.text)
      && entry.text !== row.task);
    return { action, thought: thought?.text || "" };
  }

  function visibleRows(rows) {
    return rows.filter(row => {
      if (row.type === "agent_job") return terminal.has(row.status);
      if (row.type === "routing") return false;
      // Older servers/replayed sessions may still emit this narration telemetry.
      return !(row.type === "sys" && row.text === "Voicing an agent job summary.");
    });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function inline(parent, text) {
    // Deliberately small Markdown subset: safe links, code, and emphasis.
    const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*/g;
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(offset, match.index)));
      if (match[1]) {
        const link = el("a", "", match[1]);
        link.href = match[2]; link.target = "_blank"; link.rel = "noopener noreferrer";
        parent.append(link);
      } else parent.append(el(match[3] ? "code" : "strong", "", match[3] || match[4]));
      offset = match.index + match[0].length;
    }
    parent.append(document.createTextNode(text.slice(offset)));
  }

  function formatted(text) {
    const body = el("div", "conversation-body");
    let code = null;
    let list = null;
    for (const line of String(text || "").split("\n")) {
      if (/^\s*```/.test(line)) {
        if (code) code = null;
        else { code = el("code"); const pre = el("pre"); pre.append(code); body.append(pre); }
        list = null; continue;
      }
      if (code) { code.append(document.createTextNode(line + "\n")); continue; }
      const item = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)/);
      if (item) {
        if (!list) { list = el(/^\s*\d/.test(line) ? "ol" : "ul"); body.append(list); }
        const li = el("li"); inline(li, item[1]); list.append(li);
      } else {
        list = null;
        const paragraph = el("p"); inline(paragraph, line || "\u00a0"); body.append(paragraph);
      }
    }
    return body;
  }

  function createView({ store, log, newMessages, live, activity, inspect, decide, cancel }) {
    const nodes = new Map();
    const announced = new Map();
    const activityNodes = new Map();
    let wasAtBottom = true;
    function scrollHost() {
      let node = log;
      while (node.parentElement && (node.scrollHeight <= node.clientHeight + 2 || !/auto|scroll/.test(getComputedStyle(node).overflowY))) node = node.parentElement;
      return node;
    }
    function bottom() { const host = scrollHost(); return host.scrollHeight - host.scrollTop - host.clientHeight < 64; }
    document.addEventListener("scroll", () => {
      wasAtBottom = bottom();
      if (wasAtBottom) newMessages.hidden = true;
    }, true);
    newMessages.addEventListener("click", () => {
      const host = scrollHost(); host.scrollTop = host.scrollHeight; wasAtBottom = true; newMessages.hidden = true;
    });
    function button(label, action, key = label, icon = null) {
      const node = el("button", `conversation-action${icon ? " icon-only" : ""}`, icon ? null : label);
      node.type = "button"; node.dataset.action = key;
      if (icon) {
        node.append(root.RapIcons.node(icon));
        node.setAttribute("aria-label", label);
        node.title = label;
      }
      node.addEventListener("click", action); return node;
    }
    function copy(text) {
      return button("Copy text", async () => {
        try { await navigator.clipboard.writeText(text); live.textContent = "Text copied."; }
        catch { live.textContent = "Copy unavailable. Select the text to copy it."; }
      }, "copy", "copy");
    }
    function exchange(row) {
      const body = el("div", "conversation-exchange");
      for (const event of Object.values(row.exchanges || { update: row })) {
        const label = { asked: "asks", answered: "answers", refused: "request declined", unanswered: "could not answer" }[event.event] || "update";
        body.append(el("strong", "", `${event.agent || "Agent"} → ${event.peer || "Agent"} · ${label}`));
        body.append(formatted(event.answer || event.reason || event.question || "No details reported."));
      }
      return body;
    }
    function renderActivity(activeJobs) {
      if (!activity) return;
      activity.hidden = !activeJobs.length;
      const keys = new Set(activeJobs.map(row => row.key));
      for (const [key, node] of activityNodes) {
        if (!keys.has(key)) { node.remove(); activityNodes.delete(key); }
      }
      for (const row of activeJobs) {
        let node = activityNodes.get(row.key);
        if (!node) {
          node = el("div", "live-job"); node.dataset.key = row.key;
          const head = el("div", "live-job-head");
          head.append(el("i", "presence-dot"), el("strong", "live-job-agent"), el("span", "live-job-state"),
            button(`Inspect ${row.agent || "harness"} output`, () => inspect(row.job_id), "inspect", "terminal"),
            button(`Cancel ${row.agent || "harness"} task`, () => cancel(row.job_id), "cancel", "close"));
          node.append(head, el("p", "live-job-action"), el("p", "live-job-thought"));
          activity.append(node); activityNodes.set(row.key, node);
        }
        const detail = progress(row);
        node.dataset.status = row.state || row.status || "running";
        node.querySelector(".live-job-agent").textContent = row.agent || "Harness";
        node.querySelector(".live-job-state").textContent = `${statusLabels[row.state] || statusLabels[row.status] || "Working"} · ${row.job_id}`;
        node.querySelector(".live-job-agent").title = row.task || "";
        for (const [selector, text] of [[".live-job-action", detail.action], [".live-job-thought", detail.thought]]) {
          const line = node.querySelector(selector);
          if (line.textContent !== text) line.textContent = text;
          line.title = text; line.hidden = !text;
        }
        const announcement = `${row.agent || "Harness"}: ${detail.action}`;
        if (announced.get(row.key) !== announcement) {
          live.textContent = announcement; announced.set(row.key, announcement);
        }
      }
    }
    function build(row) {
      const article = el("article", "conversation-row");
      article.dataset.key = row.key;
      if (row.type === "transcript") article.dataset.actor = row.role === "user" ? "user" : row.role === "agent" ? "harness" : "persona";
      else if (row.type === "agent_job" || row.type === "agent_consult" || row.type.startsWith("agent_confirm") || row.type === "routing") article.dataset.actor = "harness";
      const header = el("header", "conversation-header");
      let name = row.speaker_name || (row.role === "user" ? "You" : "Assistant (unknown)");
      let label = "";
      if (row.type === "agent_job") {
        name = row.agent || "Agent"; label = outcome(row).status;
        article.classList.add("conversation-task"); article.dataset.status = row.status || "running";
      } else if (row.type === "transcript") {
        label = row.role === "user" ? (row.input_mode === "voice" ? "Voice" : "Message") : deliveryLabels[row.delivery] || "Playback unconfirmed";
        article.classList.add(row.role === "user" ? "conversation-user" : row.role === "agent" ? "conversation-agent" : "conversation-persona");
      } else if (row.type.startsWith("agent_confirm")) {
        name = row.agent || "Agent";
        label = row.type === "agent_confirm" ? "Needs your approval" : ({ approve: "Approved", deny: "Denied" }[row.decision] || row.reason || "Resolved");
        article.classList.add("conversation-approval");
      } else if (row.type === "agent_consult") { name = "Agent exchange"; }
      else if (row.type === "routing") { name = "Routing"; label = row.action === "confirm" ? "Awaiting approval" : `Selected ${row.agent || "agent"}`; }
      else name = "System";
      header.append(el("strong", "conversation-name", name));
      if (row.type === "transcript" && row.source_agent && row.speaker_id !== row.source_agent) {
        header.append(el("span", "conversation-state", row.relation === "summary" ? "summarizing" : "relaying"));
        const source = el("strong", "actor-name", row.source_agent);
        source.dataset.actor = "harness"; header.append(source);
      }
      const stateLabel = el("span", "conversation-state", label);
      if (row.type === "transcript" && !["playing", "queued", "interrupted", "failed"].includes(row.delivery)) {
        stateLabel.classList.add("conversation-delivery");
      }
      header.append(stateLabel);
      if (row.occurred_at) {
        const time = el("time", "conversation-time", new Date(row.occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
        time.dateTime = row.occurred_at; header.append(time);
      }
      article.append(header);
      if (row.type === "agent_job") {
        header.title = [row.task, row.machine, row.job_id].filter(Boolean).join(" · ");
        const result = outcome(row);
        if (terminal.has(row.status)) {
          article.append(formatted(result.text));
          const partial = row.result && row.failure_detail && row.result !== row.failure_detail;
          if (partial) {
            const details = el("details", "conversation-result"); details.dataset.detail = "partial";
            details.append(el("summary", "", "Partial result"), formatted(row.result)); article.append(details);
          }
          header.append(copy(partial ? `${result.text}\n\nPartial result\n${row.result}` : result.text));
          if (row.activity?.length) {
            const history = el("details", "conversation-activity"); history.dataset.detail = "activity";
            history.append(el("summary", "", "Activity history"));
            const list = el("ol");
            for (const entry of row.activity) list.append(el("li", "", entry.text));
            history.append(list); article.append(history);
          }
        }
        header.append(button("Inspect technical output", () => inspect(row.job_id), "inspect", "terminal"));
      } else if (row.type === "agent_consult") article.append(exchange(row));
      else if (row.type.startsWith("agent_confirm")) {
        article.append(formatted(row.task || ""), el("p", "conversation-meta", row.reason || ""));
        if (row.type === "agent_confirm") {
          article.append(button("Approve", () => decide("approve", row.token)), button("Deny", () => decide("deny", row.token)));
        }
      } else if (row.type === "routing") {
        article.append(formatted(row.task || ""), el("p", "conversation-meta", row.reason || `Routing source: ${row.source || "not reported"}`));
      } else {
        const linkedResult = row.relation === "summary" && row.final !== false && row.delivery !== "playing"
          && [...store.rows.values()].some(job => job.type === "agent_job" && job.job_id === row.job_id && terminal.has(job.status));
        if (linkedResult) {
          article.classList.add("conversation-relay");
          const relay = el("details"); relay.dataset.detail = "relay";
          relay.append(el("summary", "", "Read summary"), formatted(row.text || ""));
          article.append(relay);
        } else article.append(formatted(row.text || ""));
        if (row.spoken_text != null && row.spoken_text !== row.text) {
          const spoken = el("details"); spoken.dataset.detail = "spoken";
          spoken.append(el("summary", "", "Reported spoken text"), formatted(row.spoken_text)); article.append(spoken);
        }
        if (row.type === "transcript") {
          header.append(copy(row.text || ""));
          if (row.job_id) header.append(button(`Open task ${row.job_id}`, () => inspect(row.job_id), "task", "link"));
        }
      }
      return article;
    }
    return {
      render({ announce = true } = {}) {
        const follow = wasAtBottom;
        const host = scrollHost();
        const top = host.scrollTop;
        const anchor = [...log.children].find(node => node.dataset.key && node.getBoundingClientRect().bottom > host.getBoundingClientRect().top);
        const anchorKey = anchor?.dataset.key;
        const anchorTop = anchor?.getBoundingClientRect().top;
        const all = [...store.rows.values()];
        const activeJobs = all.filter(row => row.type === "agent_job" && !terminal.has(row.status));
        renderActivity(activeJobs);
        const visible = visibleRows(all);
        const seen = new Set();
        let changed = false;
        for (const row of visible) {
          seen.add(row.key);
          const fingerprint = JSON.stringify(row);
          const old = nodes.get(row.key);
          if (old?.fingerprint === fingerprint) continue;
          const node = build(row);
          if (old) {
            const opens = new Map([...old.node.querySelectorAll("details")].map(d => [d.dataset.detail, d.open]));
            for (const d of node.querySelectorAll("details")) if (opens.has(d.dataset.detail)) d.open = opens.get(d.dataset.detail);
            const focused = old.node.contains(document.activeElement) ? document.activeElement.dataset.action : null;
            const focusedDetail = old.node.contains(document.activeElement) && document.activeElement.tagName === "SUMMARY" ? document.activeElement.parentElement.dataset.detail : null;
            old.node.replaceWith(node);
            if (focused) [...node.querySelectorAll("button")].find(b => b.dataset.action === focused)?.focus({ preventScroll: true });
            if (focusedDetail) [...node.querySelectorAll("details")].find(d => d.dataset.detail === focusedDetail)?.querySelector("summary")?.focus({ preventScroll: true });
          } else log.append(node);
          nodes.set(row.key, { node, fingerprint }); changed = true;
          const announcement = `${row.speaker_name || row.agent || "Update"}: ${row.type === "agent_job" ? `${outcome(row).status}. ${row.action || ""}` : row.text || row.reason || row.action || ""}`;
          if (announce && announced.get(row.key) !== announcement && (row.type !== "transcript" || row.final || /[.!?]\s*$/.test(row.text || ""))) {
            live.textContent = announcement;
            announced.set(row.key, announcement);
          }
        }
        for (const [key, entry] of nodes) if (!seen.has(key)) { entry.node.remove(); nodes.delete(key); announced.delete(key); }
        log.querySelector(".empty-state")?.remove();
        if (!visible.length) {
          log.append(el("div", "empty-state", "Conversation is ready. Speak or type a message."));
          wasAtBottom = true; newMessages.hidden = true; return;
        }
        if (follow) { const currentHost = scrollHost(); currentHost.scrollTop = currentHost.scrollHeight; newMessages.hidden = true; }
        else {
          host.scrollTop = top;
          const retained = nodes.get(anchorKey)?.node;
          if (retained) host.scrollTop += retained.getBoundingClientRect().top - anchorTop;
          if (changed) newMessages.hidden = false;
        }
      },
    };
  }
  // -- Task 9: unified conversation-hub transcript and controls --------------
  // A second, independent store/view: different data shape (server-merged
  // hub+live entries, not raw pipeline events) and different refresh cadence
  // (fetched wholesale on demand, not polled incrementally).

  function resolveChannelSelection(channels, requestedId) {
    if (!requestedId) return "";
    return (channels || []).some((channel) => channel.channel_id === requestedId) ? requestedId : "";
  }

  function memoryDetailFields(memory) {
    const fields = [
      ["Scope", memory.scope],
      ["Confidence", memory.confidence],
      ["Status", memory.status],
      ["Observed", memory.observed_at],
      ["Source turns", (memory.source_turn_ids || []).join(", ") || "none"],
    ];
    if (memory.supersedes) fields.push(["Supersedes", memory.supersedes]);
    if (memory.eligibility) fields.push(["Eligibility", memory.eligibility]);
    return fields;
  }

  function historyEntryNode(entry, { onMemoryClick } = {}) {
    const article = el("article", "conversation-row history-entry");
    article.dataset.key = entry.entry_id;
    article.dataset.source = entry.source;
    const header = el("header", "conversation-header");
    header.append(el("strong", "conversation-name", entry.speaker_id || "Unknown"));
    if (entry.result_kind) header.append(el("span", "conversation-state history-result", entry.result_kind));
    if (entry.playback_state) {
      header.append(el("span", "conversation-state conversation-delivery", deliveryLabels[entry.playback_state] || entry.playback_state));
    }
    if (entry.occurred_at) {
      const time = el("time", "conversation-time", new Date(entry.occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      time.dateTime = entry.occurred_at;
      header.append(time);
    }
    article.append(header);
    article.append(formatted(entry.full_text || ""));
    if (entry.spoken_text != null && entry.spoken_text !== entry.full_text) {
      const spoken = el("details");
      spoken.dataset.detail = "spoken";
      spoken.append(el("summary", "", "Reported spoken text"), formatted(entry.spoken_text));
      article.append(spoken);
    }
    if (entry.memory_id && onMemoryClick) {
      const link = el("button", "conversation-action history-memory-link", "Related memory");
      link.type = "button";
      link.addEventListener("click", () => onMemoryClick(entry.memory_id));
      article.append(link);
    }
    return article;
  }

  function channelActionsNode(channel, { onAction } = {}) {
    const row = el("div", "history-channel-actions");
    row.dataset.channel = channel.channel_id;
    // Archiving is one-way -- the hub has no "unarchive" action -- so an
    // already-archived channel gets a disabled button rather than a label
    // that implies re-clicking it would reopen the channel.
    const archive = el("button", "conversation-action history-archive", channel.archived ? "Archived" : "Archive");
    archive.type = "button";
    archive.disabled = channel.archived;
    archive.addEventListener("click", () => onAction?.("archive", channel.channel_id));
    const chapter = el("button", "conversation-action history-new-chapter", "New chapter");
    chapter.type = "button";
    chapter.addEventListener("click", () => onAction?.("new_chapter", channel.channel_id));
    row.append(archive, chapter);
    if (channel.resettable) {
      const reset = el("button", "conversation-action history-reset destructive", "Reset session");
      reset.type = "button";
      reset.addEventListener("click", () => onAction?.("session_reset", channel.channel_id));
      row.append(reset);
    }
    return row;
  }

  function createHistoryView({ list, channelSelect, search, actions, onAction, onMemoryClick }) {
    let channels = [];
    function renderChannelOptions() {
      if (!channelSelect) return;
      const requested = channelSelect.value;
      channelSelect.innerHTML = "";
      const allOption = document.createElement("option");
      allOption.value = "";
      allOption.textContent = "All channels";
      channelSelect.append(allOption);
      for (const channel of channels) {
        const option = document.createElement("option");
        option.value = channel.channel_id;
        option.textContent = `${channel.channel_id} (ch. ${channel.chapter_id})${channel.archived ? " · archived" : ""}`;
        channelSelect.append(option);
      }
      channelSelect.value = resolveChannelSelection(channels, requested);
    }
    return {
      get channelId() { return channelSelect ? channelSelect.value : ""; },
      get query() { return search ? search.value.trim() : ""; },
      setChannels(nextChannels) {
        channels = nextChannels || [];
        renderChannelOptions();
        if (actions) {
          actions.innerHTML = "";
          for (const channel of channels) {
            actions.append(channelActionsNode(channel, { onAction }));
          }
        }
      },
      render(entries) {
        list.innerHTML = "";
        if (!entries.length) {
          list.append(el("div", "empty-state", "No conversation history matches."));
          return;
        }
        for (const entry of entries) list.append(historyEntryNode(entry, { onMemoryClick }));
      },
    };
  }

  const api = {
    createStore,
    createView,
    outcome,
    deliveryLabels,
    progress,
    visibleRows,
    createHistoryView,
    resolveChannelSelection,
    memoryDetailFields,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RapConversation = api;
})(typeof window === "undefined" ? globalThis : window);
