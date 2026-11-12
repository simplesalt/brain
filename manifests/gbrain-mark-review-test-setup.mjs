import { SQL } from 'bun';
const db = new SQL(process.env.DATABASE_URL);
if (!/@gbrain-pg-rehearsal-rw[.:/]/.test(process.env.DATABASE_URL)) { console.log('FATAL: not the rehearsal database'); process.exit(1); }
const MCP = process.env.GBRAIN_MCP_URL;
const put = async (slug, content) => {
  const r = await fetch(MCP, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', Authorization: 'Bearer gbrain-mcp' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'put_page', arguments: { slug, content } } }) });
  console.log('put_page', slug, r.status, (await r.text()).slice(0, 160).replace(/\n/g, ' '));
};
await db`CREATE TABLE IF NOT EXISTS addon_page_marks (
  id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('delete', 'replace')), slug TEXT NOT NULL, source_id TEXT NOT NULL,
  reason TEXT NOT NULL, content TEXT, principal TEXT NOT NULL, page_bytes BIGINT NOT NULL, page_revision TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), withdrawn_at TIMESTAMPTZ, applied_at TIMESTAMPTZ, outcome TEXT)`;
await db`DELETE FROM addon_page_marks WHERE slug LIKE 'notes/mark-review-test-%'`;
const T = Date.now();
const slugs = ['a-delete-due', 'b-withdrawn', 'c-replace-due', 'd-delete-not-due', 'e-changed-since'].map(s => `notes/mark-review-test-${s}-${T}`);
for (const s of slugs) await put(s, `---\ntype: note\ntitle: Mark review test ${s}\n---\n\n# ${s}\n\nOriginal content for the T3 rehearsal test.\n`);
await Bun.sleep(3000);
const rev = async (s) => (await db`SELECT to_jsonb(p)->>'knowledge_revision' AS r FROM pages p WHERE slug = ${s} AND source_id = 'default' AND deleted_at IS NULL`)[0]?.r ?? null;
const ins = (kind, s, hoursAgo, content = null) => db`INSERT INTO addon_page_marks (kind, slug, source_id, reason, content, principal, page_bytes, page_revision, created_at)
  VALUES (${kind}, ${s}, 'default', 'T3 rehearsal test', ${content}, 'test', 60, ${null}, now() - make_interval(hours => ${hoursAgo}))`;
for (const [kind, s, h, content] of [['delete', slugs[0], 5], ['delete', slugs[1], 5], ['replace', slugs[2], 5, `---\ntype: note\ntitle: Replaced\n---\n\n# Replaced by the T3 test\n`], ['delete', slugs[3], 0], ['delete', slugs[4], 5]]) {
  await ins(kind, s, h, content ?? null);
  await db`UPDATE addon_page_marks SET page_revision = ${await rev(s)} WHERE slug = ${s}`;
}
await db`UPDATE addon_page_marks SET withdrawn_at = now(), outcome = 'withdrawn' WHERE slug = ${slugs[1]}`;
await put(slugs[4], `---\ntype: note\ntitle: Mark review test changed\n---\n\n# changed\n\nChanged after the mark.\n`);
await Bun.sleep(3000);
console.log('marks:', JSON.stringify(await db`SELECT id, kind, slug, page_revision, created_at::text, withdrawn_at::text FROM addon_page_marks WHERE slug LIKE 'notes/mark-review-test-%' ORDER BY id`));
await Bun.write('/work/slugs.json', JSON.stringify(slugs));
await db.end();
