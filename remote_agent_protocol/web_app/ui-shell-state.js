(function installUiShell(root) {
  "use strict";

  const VIEW_TITLES = Object.freeze({
    control: "Control Center",
    agents: "Agents",
    personas: "Personas",
    memory: "Memory",
    setup: "Setup",
    status: "Status",
    settings: "Settings",
  });

  function viewTitle(view) {
    return VIEW_TITLES[view] || "Remote Agent Protocol";
  }

  function applyResponsiveDisclosures({ width, health, activity, previous = {} }) {
    const next = { health: width <= 1100, activity: width <= 1100 };
    if (health && previous.health !== next.health) health.open = !next.health;
    if (activity && previous.activity !== next.activity) activity.open = !next.activity;
    return next;
  }

  function serviceFailed(service) {
    return service?.ok === false && /fail|error|offline|unavailable|stopped/i.test(service?.label || service?.error || "");
  }

  function healthSummary(status, connectionLost = false) {
    if (connectionLost) return { tone: "status-error", label: "Offline" };
    if (["failed", "stopped"].includes(status?.session)
      || serviceFailed(status?.health)
      || serviceFailed(status?.ttsHealth)
      || status?.vram?.percent >= 95) {
      return { tone: "status-error", label: "Service failure" };
    }
    if (!status || status.session !== "ready" || status.health?.ok === false
      || status.ttsHealth?.ok === false || status?.vram?.percent >= 85) {
      return { tone: "status-warning", label: status?.session === "starting" ? "Starting" : "Check system" };
    }
    const active = status.activeAgentCount || 0;
    return { tone: "status-success", label: active ? `${active} agents active` : "All systems ready" };
  }

  function createMomentaryControl(setActive) {
    let active = false;
    function start() {
      if (active) return;
      active = true;
      setActive(true);
    }
    function stop() {
      if (!active) return;
      active = false;
      setActive(false);
    }
    function keyDown(event) {
      if (!["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (!event.repeat) start();
    }
    function keyUp(event) {
      if (!["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      stop();
    }
    return { start, stop, cancel: stop, keyDown, keyUp };
  }

  function setModalState({ shell, body, open, opener }) {
    if (shell) shell.inert = open;
    body?.classList?.toggle("command-palette-open", open);
    if (!open) opener?.focus?.();
  }

  function focusableElements(container) {
    if (!container) return [];
    return [...container.querySelectorAll(
      'button:not([disabled]):not([tabindex="-1"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )];
  }

  function trapModalTab(event, container) {
    if (event.key !== "Tab") return false;
    const focusable = focusableElements(container);
    if (!focusable.length) return false;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && event.target === first) {
      event.preventDefault();
      last.focus();
      return true;
    }
    if (!event.shiftKey && event.target === last) {
      event.preventDefault();
      first.focus();
      return true;
    }
    return false;
  }

  root.RapUiShell = {
    applyResponsiveDisclosures,
    createMomentaryControl,
    healthSummary,
    setModalState,
    trapModalTab,
    viewTitle,
  };
})(globalThis);
