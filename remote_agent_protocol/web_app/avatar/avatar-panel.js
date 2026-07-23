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
    showFallback(show, reason = "renderer-unavailable") {
      fallback?.classList.toggle("active", show);
      fallback?.setAttribute("aria-hidden", String(!show));
      fallback?.setAttribute("role", show ? "img" : "presentation");
      fallback?.setAttribute("aria-label", show ? `Static assistant companion: ${reason}` : "");
      host?.classList.toggle("has-fallback", show);
    },
    render(runtime, resolved, labelsVisible = true) {
      const personaName = runtime.persona || "Assistant";
      const state = resolved.state || "idle";
      const emotion = resolved.emotion?.name || "neutral";
      if (persona) persona.textContent = personaName;
      if (stateLabel) stateLabel.textContent = state;
      if (emotionLabel) emotionLabel.textContent = emotion;
      host?.setAttribute(
        "aria-label",
        labelsVisible
          ? `Animated assistant face for ${personaName}`
          : `Animated assistant face for ${personaName}, ${state}, ${emotion}`,
      );
    },
    setCollapsed,
    setEnabled(enabled) { panel?.classList.toggle("hidden", !enabled); },
    setLabelsVisible(visible) { panel?.classList.toggle("hide-state-labels", !visible); },
    onCollapse(handler) { collapse?.addEventListener("click", handler); },
  };
}
