export function createAvatarPanel() {
  const panel = document.getElementById("avatarPanel");
  const body = document.getElementById("avatarPanelBody");
  const fallback = document.getElementById("avatarFallback");
  const host = document.getElementById("avatarCanvasHost");
  const collapse = document.getElementById("avatarCollapseBtn");
  const persona = document.getElementById("avatarPersonaName");
  const stateLabel = document.getElementById("avatarStateLabel");
  const emotionLabel = document.getElementById("avatarEmotionLabel");

  function setCollapsed(value) {
    panel?.classList.toggle("collapsed", value);
    body?.classList.toggle("hidden", value);
    collapse?.setAttribute("aria-expanded", String(!value));
    collapse?.setAttribute("aria-label", value ? "Expand companion" : "Collapse companion");
    if (collapse) collapse.textContent = value ? "+" : "−";
  }

  return {
    host,
    showFallback(show) {
      fallback?.classList.toggle("active", show);
      fallback?.setAttribute("aria-hidden", String(!show));
    },
    render(runtime, resolved) {
      if (persona) persona.textContent = runtime.persona || "Assistant";
      if (stateLabel) stateLabel.textContent = resolved.state || "idle";
      if (emotionLabel) emotionLabel.textContent = resolved.emotion?.name || "neutral";
    },
    setCollapsed,
    setEnabled(enabled) { panel?.classList.toggle("hidden", !enabled); },
    setLabelsVisible(visible) { panel?.classList.toggle("hide-state-labels", !visible); },
    onCollapse(handler) { collapse?.addEventListener("click", handler); },
  };
}
