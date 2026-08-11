(function installInputControls(root) {
  "use strict";

  function modeLabel(mode) {
    return mode === "wake_word" ? "Wake Word" : mode === "push_to_talk" ? "Push To Talk" : "Free Talk";
  }

  function confirmedLabel(action, status) {
    if (action === "voice_mode") return `${modeLabel(status?.voiceMode)} remains active.`;
    return `${status?.muted ? "Mic muted" : "Mic live"} remains active.`;
  }

  function pendingLabel(pending) {
    if (pending?.action === "voice_mode") return `Switching to ${modeLabel(pending.payload.mode)}…`;
    return pending?.payload.muted ? "Muting external microphone…" : "Unmuting external microphone…";
  }

  function deriveInputControlView({ status, pending = null, error = "", connectionLost = false }) {
    if (connectionLost) {
      return {
        disabled: true,
        hidden: false,
        tone: "error",
        text: error || "Connection lost. Audio controls are disabled until RAP reconnects.",
      };
    }
    if (pending) {
      return { disabled: true, hidden: false, tone: "warning", text: pendingLabel(pending) };
    }
    if (status?.mode === "brain" && status?.inputControl?.modeReady === false) {
      return {
        disabled: true,
        hidden: false,
        tone: "error",
        text: error || "Input mode synchronization is unconfirmed. Audio controls are locked.",
      };
    }
    if (error) return { disabled: false, hidden: false, tone: "error", text: error };
    if (status?.mode === "brain" && status?.inputControl?.muteReady === false) {
      return {
        disabled: false,
        hidden: false,
        tone: "error",
        text: "External microphone mute state is unconfirmed. Treat the microphone as live.",
      };
    }
    if (status?.voiceMode === "wake_word" && status?.wake?.phase === "error") {
      return {
        disabled: false,
        hidden: false,
        tone: "error",
        text: `Wake Word unavailable: ${status.wake.error || "detector failed"}.`,
      };
    }
    return { disabled: false, hidden: true, tone: "", text: "" };
  }

  function createInputControlCoordinator(options) {
    let pending = null;
    let error = "";

    function view() {
      return deriveInputControlView({
        status: options.getStatus(),
        pending,
        error,
        connectionLost: options.getConnectionLost(),
      });
    }

    function render() {
      options.render(view());
    }

    async function run(action, payload) {
      if (pending || options.getConnectionLost()) return { ignored: true };
      pending = { action, payload };
      error = "";
      render();
      try {
        const data = await options.request(action, payload);
        if (data?.status) options.setStatus(data.status);
        if (!data?.ok) {
          error = `${data?.error || "Input control change was rejected."} ${confirmedLabel(action, options.getStatus())}`;
        }
        return data;
      } catch (cause) {
        options.setConnectionLost(true);
        error = `Connection lost while updating audio controls: ${cause?.message || cause}.`;
        return { ok: false, error };
      } finally {
        pending = null;
        render();
      }
    }

    function connected() {
      options.setConnectionLost(false);
      error = "Connected. Audio controls synchronized.";
      render();
    }

    return { run, connected, render, view };
  }

  root.RapInputControls = { createInputControlCoordinator, deriveInputControlView };
})(globalThis);
