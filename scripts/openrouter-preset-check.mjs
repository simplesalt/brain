// One-shot: each app's OpenRouter key calls each of the four presets once (plus the vision
// model on the Hermes key), with a tiny prompt. Logs only key label, preset, HTTP status, the
// provider and model OpenRouter routed to, and OpenRouter's error message; never a key or a body.
// To rerun: restore manifests/openrouter-preset-check.yaml and its kustomization entries from commit
// 1485cf1 with a bumped Job suffix, then remove them again once the Jobs finish (Flux recreates a
// Job its TTL deleted).
const KEYS = JSON.parse(process.env.KEYS);
const MODELS = ["@preset/live-reasoning", "@preset/live-dumb", "@preset/slow-reasoning", "@preset/slow-dumb"];
async function one(label, key, model) {
  const t0 = Date.now();
  let status = 0, j = {};
  try {
    const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ model, max_tokens: 2000, messages: [{ role: "user", content: "Reply with the single word OK." }] }),
      signal: AbortSignal.timeout(120000),
    });
    status = res.status;
    try { j = await res.json(); } catch (e) {}
  } catch (e) { j = { error: { message: String(e) } }; }
  const ok = status === 200 && !j.error && j.choices && j.choices.length > 0;
  console.log(JSON.stringify({ key: label, model, result: ok ? "PASS" : "FAIL", status,
    provider: j.provider || null, routed_model: j.model || null, ms: Date.now() - t0,
    error: j.error ? String(j.error.message || "").slice(0, 300) : null }));
}
for (const [label, env, extra] of KEYS) {
  const key = (process.env[env] || "").trim();
  if (!key) { console.log(JSON.stringify({ key: label, result: "SKIP", error: "key not set" })); continue; }
  for (const m of MODELS.concat(extra || [])) await one(label, key, m);
}
