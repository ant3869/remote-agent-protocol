/* Shared local SVG icons; labels belong on the surrounding control. */
(function (root) {
  "use strict";
  const paths = {
    home: ["m3 10 9-7 9 7", "M5 9v12h5v-7h4v7h5V9"],
    agents: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87", "M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0"],
    persona: ["m4 4 8-2 8 2v7c0 6-8 11-8 11S4 17 4 11Z", "M7 9h2m6 0h2m-8 6q3 2 6 0"],
    memory: ["M20 5c0 2-3.6 3-8 3S4 7 4 5s3.6-3 8-3 8 1 8 3Z", "M4 5v14c0 2 3.6 3 8 3s8-1 8-3V5M4 12c0 2 3.6 3 8 3s8-1 8-3"],
    activity: ["M2 12h5l3-9 4 18 3-9h5"],
    settings: ["m9 3 1-1h4l1 3 3 1 3-1 2 4-2 2v3l2 2-2 4-3-1-3 1-1 2h-4l-1-2-3-1-3 1-2-4 2-2v-3L1 9l2-4 3 1 3-1Z", "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0"],
    terminal: ["M3 3h18v18H3Z", "m7 8 4 4-4 4m6 0h4"],
    copy: ["M9 9h12v12H9Z", "M15 5V3H3v12h2"],
    link: ["M14 3h7v7m0-7L10 14", "M10 3H3v18h18v-7"],
    plus: ["M12 5v14M5 12h14"],
    more: ["M5 11v2m7-2v2m7-2v2"],
    close: ["m6 6 12 12M6 18 18 6"],
    search: ["M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0m-2 5 6 6"],
    mic: ["M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0Z", "M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"],
    cpu: ["M6 6h12v12H6Zm3 3h6v6H9Z", "M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"],
    power: ["M12 2v10M6 5a9 9 0 1 0 12 0"],
    download: ["M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"],
    refresh: ["M20 7v5h-5M4 17v-5h5", "M5 7a8 8 0 0 1 13-2l2 3M4 16l2 3a8 8 0 0 0 13-2"],
    sliders: ["M4 4v4m0 4v8M12 4v8m0 4v4M20 4v2m0 4v10", "M1 8h6v4H1Zm8 4h6v4H9Zm8-6h6v4h-6Z"],
    speaker: ["m11 3-6 5H2v8h3l6 5Z", "M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"],
  };
  function svg(name) {
    const lines = paths[name] || paths.terminal;
    return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${lines.map(d => `<path d="${d}"/>`).join("")}</svg>`;
  }
  function node(name) {
    const template = document.createElement("template");
    template.innerHTML = svg(name); // Only the fixed paths above can enter the markup.
    return template.content.firstElementChild;
  }
  function hydrate(scope = document) {
    scope.querySelectorAll("[data-icon]").forEach(host => host.replaceChildren(node(host.dataset.icon)));
  }
  const api = { svg, node, hydrate };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else { root.RapIcons = api; hydrate(); }
})(typeof window === "undefined" ? globalThis : window);
