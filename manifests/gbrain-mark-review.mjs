import { SQL } from 'bun';
import { readdirSync, statSync } from 'fs';

const env = process.env;
const APPLY = env.APPLY === 'true';
const DELETE_AFTER_HOURS = Number(env.DELETE_AFTER_HOURS ?? 24 * 180);
const REPLACE_AFTER_HOURS = Number(env.REPLACE_AFTER_HOURS ?? 24 * 7);
const BACKUP_MAX_AGE_HOURS = Number(env.BACKUP_MAX_AGE_HOURS ?? 26);
const BACKUP_DIR = env.BACKUP_DIR ?? '/dumps';
const MCP = env.GBRAIN_MCP_URL;
const TOKEN = env.GBRAIN_REMOTE_TOKEN;
const log = (o) => console.log(JSON.stringify({ at: new Date().toISOString(), ...o }));

const db = new SQL(env.DATABASE_URL);

async function mcp(name, args) {
  const r = await fetch(MCP, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } }),
  });
  const t = await r.text();
  let msg = null;
  for (const b of t.split('\n\n')) {
    const d = b.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('\n');
    if (!d) continue;
    try { const o = JSON.parse(d); if (o.result || o.error) msg = o; } catch {}
  }
  if (!msg) { try { msg = JSON.parse(t); } catch { return { isError: true, text: `HTTP ${r.status}: ${t.slice(0, 300)}` }; } }
  if (msg.error) return { isError: true, text: JSON.stringify(msg.error) };
  return { isError: !!msg.result?.isError, text: (msg.result?.content ?? []).map(c => c.text ?? '').join('\n') };
}

function newestBackupAgeHours() {
  let newest = 0;
  try {
    for (const f of readdirSync(BACKUP_DIR)) {
      if (!/^gbrain-nightly-.*\.dump$/.test(f)) continue;
      try { statSync(`${BACKUP_DIR}/${f}.sha256`); } catch { continue; }
      newest = Math.max(newest, statSync(`${BACKUP_DIR}/${f}`).mtimeMs);
    }
  } catch { return null; }
  return newest ? (Date.now() - newest) / 3_600_000 : null;
}

await db`CREATE TABLE IF NOT EXISTS addon_page_marks (
  id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('delete', 'replace')), slug TEXT NOT NULL, source_id TEXT NOT NULL,
  reason TEXT NOT NULL, content TEXT, principal TEXT NOT NULL, page_bytes BIGINT NOT NULL, page_revision TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), withdrawn_at TIMESTAMPTZ, applied_at TIMESTAMPTZ, outcome TEXT)`;

const backupAge = newestBackupAgeHours();
log({ event: 'start', mode: APPLY ? 'apply' : 'log-only', delete_after_hours: DELETE_AFTER_HOURS, replace_after_hours: REPLACE_AFTER_HOURS,
  newest_backup_age_hours: backupAge === null ? null : Math.round(backupAge * 10) / 10, mcp: MCP });

const open = await db`SELECT id, kind, slug, source_id, reason, content, page_revision, page_bytes, created_at,
    extract(epoch FROM now() - created_at) / 3600 AS age_hours
  FROM addon_page_marks WHERE withdrawn_at IS NULL AND applied_at IS NULL ORDER BY created_at`;
log({ event: 'open_marks', count: open.length });

const counts = {};
const bump = (k) => { counts[k] = (counts[k] ?? 0) + 1; };

for (const m of open) {
  const base = { mark_id: Number(m.id), kind: m.kind, slug: m.slug, source_id: m.source_id, reason: m.reason, age_hours: Math.round(Number(m.age_hours) * 10) / 10 };
  const due = Number(m.age_hours) >= (m.kind === 'delete' ? DELETE_AFTER_HOURS : REPLACE_AFTER_HOURS);
  if (!due) { log({ ...base, outcome: 'waiting' }); bump('waiting'); continue; }

  const [page] = await db`SELECT to_jsonb(p)->>'knowledge_revision' AS revision, p.deleted_at FROM pages p WHERE p.slug = ${m.slug} AND p.source_id = ${m.source_id}`;
  if (!page || page.deleted_at) {
    if (APPLY) await db`UPDATE addon_page_marks SET withdrawn_at = now(), outcome = 'page_gone' WHERE id = ${m.id}`;
    log({ ...base, outcome: 'page_gone' }); bump('page_gone'); continue;
  }
  if (m.page_revision && page.revision !== m.page_revision) {
    if (APPLY) await db`UPDATE addon_page_marks SET withdrawn_at = now(), outcome = 'page_changed_since_mark' WHERE id = ${m.id}`;
    log({ ...base, outcome: 'page_changed_since_mark', marked_revision: m.page_revision, current_revision: page.revision }); bump('page_changed_since_mark'); continue;
  }
  if (backupAge === null || backupAge > BACKUP_MAX_AGE_HOURS) {
    log({ ...base, outcome: 'held_no_recent_backup' }); bump('held_no_recent_backup'); continue;
  }
  if (!APPLY) { log({ ...base, outcome: m.kind === 'delete' ? 'would_delete' : 'would_replace' }); bump(m.kind === 'delete' ? 'would_delete' : 'would_replace'); continue; }

  const call = m.kind === 'delete'
    ? await mcp('delete_page', { slug: m.slug, source_id: m.source_id, ...(page.revision ? { expected_revision: page.revision } : {}) })
    : await mcp('put_page', { slug: m.slug, source_id: m.source_id, content: m.content, ...(page.revision ? { expected_revision: page.revision } : {}), request_id: crypto.randomUUID() });
  if (call.isError) { log({ ...base, outcome: 'apply_failed', error: call.text.slice(0, 400) }); bump('apply_failed'); continue; }
  const outcome = m.kind === 'delete' ? 'deleted' : 'replaced';
  await db`UPDATE addon_page_marks SET applied_at = now(), outcome = ${outcome} WHERE id = ${m.id}`;
  log({ ...base, outcome }); bump(outcome);
}

log({ event: 'done', counts });
await db.end();
console.log('RESULT: PASS');
