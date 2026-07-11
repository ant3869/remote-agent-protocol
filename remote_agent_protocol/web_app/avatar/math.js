export const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
export const damp = (current, target, lambda, delta) => current + (target - current) * (1 - Math.exp(-lambda * delta));
export const range = (pair, random = Math.random) => pair[0] + (pair[1] - pair[0]) * random();
export const normalizeName = (value) => String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
