import { SQL } from 'bun';
import { engineStore, ensureGuardTables, makeGuardHandler } from '/addon/src/guard.ts';

if (!/@gbrain-pg-rehearsal-rw[.:/]/.test(process.env.DATABASE_URL)) { console.log('FATAL: not the rehearsal database'); process.exit(1); }
const db = new SQL(process.env.DATABASE_URL);
const engine = { executeRaw: (q, p) => db.unsafe(q, p ?? []) };
await ensureGuardTables(engine);
const handler = makeGuardHandler(engineStore(engine, 0), { upstream: 'http://gbrain-rehearsal:3131/mcp', defaultSource: 'default', overwritePrefixes: ['ops/'] });
const server = Bun.serve({ port: 3141, fetch: handler });

const rpc = async (method, params) => {
  const r = await fetch('http://127.0.0.1:3141/mcp', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', Authorization: 'Bearer gbrain-mcp' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  const t = await r.text();
  let msg = null;
  for (const b of t.split('\n\n')) { const d = b.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('\n'); if (!d) continue; try { const o = JSON.parse(d); if (o.result || o.error) msg = o; } catch {} }
  return msg ?? JSON.parse(t);
};
const call = async (name, args) => {
  const m = await rpc('tools/call', { name, arguments: args });
  const text = (m.result?.content ?? []).map(c => c.text ?? '').join('\n');
  let body; try { body = JSON.parse(text); } catch { body = text; }
  return { isError: !!m.result?.isError || !!m.error, body: m.error ?? body };
};
const results = [];
const check = (label, ok, detail) => { results.push({ label, ok }); console.log(`${ok ? 'PASS' : 'FAIL'} ${label} :: ${JSON.stringify(detail).slice(0, 400)}`); };
const code = (r) => (typeof r.body === 'object' && r.body ? r.body.error ?? r.body.code : null);
const rev = async (slug) => (await call('get_page', { slug, body: 'none', include_deleted: true })).body?.revision;

const T = Date.now();
const note = `notes/guard-rehearsal-${T}`;
const state = `ops/guard-rehearsal-state-${T}`;

const list = await rpc('tools/list', {});
const names = list.result.tools.map(t => t.name);
check('tools/list hides delete_page and adds the mark tools', !names.includes('delete_page') && ['mark_page_for_deletion', 'withdraw_page_mark', 'propose_page_replacement'].every(n => names.includes(n)), { count: names.length });

let r = await call('put_page', { slug: note, content: `---\ntype: note\ntitle: Guard rehearsal ${T}\n---\n\n# Guard rehearsal\n\n${Array.from({ length: 40 }, (_, i) => `Line ${i} of original content for the guard rehearsal test.`).join('\n')}\n` });
check('put_page creates a new page', !r.isError, r.body?.status ?? r.body);

r = await call('put_page', { slug: note, content: 'overwrite', expected_revision: await rev(note) });
check('put_page on an existing page is refused', r.isError && code(r) === 'guard_overwrite_refused', r.body);

for (const opt of ['allow_shrink', 'force', 'allow_empty', 'drop_timeline']) {
  r = await call('put_page', { slug: `notes/guard-rehearsal-never-${T}`, content: 'x', [opt]: true });
  check(`put_page with ${opt} is refused`, r.isError && code(r) === 'guard_option_refused', r.body);
}

r = await call('delete_page', { slug: note });
check('delete_page is refused', r.isError && code(r) === 'guard_tool_refused', r.body);

r = await call('edit_page', { slug: note, expected_revision: await rev(note), edits: [{ old_text: '# Guard rehearsal', new_text: '# Guard rehearsal (edited)' }] });
check('edit_page changes part of a page', !r.isError, r.body?.status ?? r.body);

r = await call('append_page', { slug: note, text: 'Appended through the guard.', request_id: crypto.randomUUID() });
check('append_page adds to a page', !r.isError, r.body?.status ?? r.body);

const before = (await call('get_page', { slug: note, include_content: true })).body;
const body = String(before?.content ?? '');
const cut = body.slice(body.indexOf('Line 0 of original'), body.indexOf('Line 39 of original'));
r = await call('edit_page', { slug: note, expected_revision: before?.revision, edits: [{ old_text: cut, new_text: '' }] });
check('edit_page removing most of a page goes through', !r.isError, r.body?.status ?? r.body);
r = await call('get_versions', { slug: note });
const versions = Array.isArray(r.body) ? r.body : (r.body?.versions ?? []);
check('get_versions through the guard returns the earlier versions', !r.isError && versions.length >= 3, { count: versions.length, sample: versions[0] });

r = await call('put_page', { slug: state, content: '---\ntype: note\ntitle: Guard rehearsal state\n---\n\n```json\n{"n":1}\n```\n' });
check('put_page creates an ops/ state page', !r.isError, r.body?.status ?? r.body);
r = await call('put_page', { slug: state, content: '---\ntype: note\ntitle: Guard rehearsal state\n---\n\n```json\n{"n":2}\n```\n', expected_revision: await rev(state) });
check('put_page may rewrite an ops/ state page', !r.isError, r.body?.status ?? r.body);

r = await call('mark_page_for_deletion', { slug: note, reason: 'guard rehearsal test' });
check('mark_page_for_deletion records a mark', !r.isError && r.body?.status === 'marked', r.body);
r = await call('get_page', { slug: note });
check('a marked page is hidden from get_page', r.isError && code(r) === 'page_flagged_for_deletion', r.body);
r = await call('list_pages', { limit: 500, sort: 'updated_desc' });
console.log('list_pages raw shape:', typeof r.body, JSON.stringify(r.body).slice(0, 600));
check('a marked page is left out of list_pages', !JSON.stringify(r.body).includes(note), { contains: JSON.stringify(r.body).includes(note) });
r = await call('list_pages', { limit: 500, sort: 'updated_desc', include_deleted: true });
check('list_pages with include_deleted shows it', JSON.stringify(r.body).includes(note), { contains: JSON.stringify(r.body).includes(note) });
r = await call('get_page', { slug: note, include_deleted: true });
check('get_page with include_deleted reads it', !r.isError, { slug: r.body?.slug });
const [still] = await db`SELECT deleted_at FROM pages WHERE slug = ${note} AND source_id = 'default'`;
check('the marked page is not deleted in gbrain', still && still.deleted_at === null, still);
r = await call('withdraw_page_mark', { slug: note });
check('withdraw_page_mark restores it', !r.isError && r.body?.withdrawn === 1, r.body);
r = await call('get_page', { slug: note });
check('get_page reads it again after the withdrawal', !r.isError, { slug: r.body?.slug });

const failed = results.filter(x => !x.ok);
console.log(`SUMMARY: ${results.length - failed.length}/${results.length} pass${failed.length ? '; failed: ' + failed.map(x => x.label).join('; ') : ''}`);
server.stop();
await db.end();
process.exit(failed.length ? 1 : 0);
