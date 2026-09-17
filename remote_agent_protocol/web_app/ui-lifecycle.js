// pagehide covers tab/window closure; pageshow also covers back-forward cache restores.
// No inactivity timer: a background or sleeping tab still owns its voice session.
const tab = crypto.randomUUID();
let sequence = 0;
let quitting = false;

async function notify(event) {
  const response = await fetch("/api/ui-lifecycle", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-Token": window.__CSRF_TOKEN__ || "",
    },
    body: JSON.stringify({ event, tab, sequence: ++sequence }),
    keepalive: true,
  });
  if (!response.ok) throw new Error(`Lifecycle request failed (${response.status})`);
}

window.addEventListener("pageshow", () => { notify("open").catch(console.warn); });
window.addEventListener("pagehide", () => {
  if (!quitting) notify("close").catch(() => {});
});
// Module loading can finish after pageshow on slow machines.
notify("open").catch(console.warn);

document.getElementById("quitAppBtn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Stopping…";
  try {
    await notify("quit");
    quitting = true;
    document.querySelector(".app-shell").inert = true;
    const notice = document.createElement("dialog");
    notice.textContent = "Remote Agent Protocol is shutting down. You can close this tab.";
    notice.setAttribute("aria-label", "Application shutting down");
    document.body.append(notice);
    notice.showModal();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Retry quit";
    button.title = `${error.message}. You can also close the launcher window.`;
  }
});
