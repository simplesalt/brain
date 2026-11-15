import { SQL } from 'bun';

if (!/@gbrain-pg-rw[.:/]/.test(process.env.DATABASE_URL)) { console.log('FATAL: not gbrain-pg-rw'); process.exit(1); }
const db = new SQL(process.env.DATABASE_URL);
const SOURCE = 'meetings/2026-11-12-livetest-kvzq-pentest-scoping';
const TARGETS = [
  { slug: 'people/dylan-evans', entry: 6157 },
  { slug: 'companies/simple-salt', entry: 6163 },
];

const blocks = (text) => {
  const out = [];
  for (const line of (text ?? '').split('\n')) {
    if (line.startsWith('- **') || out.length === 0) out.push([line]);
    else out[out.length - 1].push(line);
  }
  return out.map(b => b.join('\n'));
};

let ok = true;
try {
  await db.begin(async (tx) => {
    for (const t of TARGETS) {
      const [p] = await tx`SELECT id, slug, timeline, octet_length(compiled_truth) AS cbytes FROM pages WHERE slug = ${t.slug} AND deleted_at IS NULL`;
      if (!p) throw new Error(`${t.slug}: page not found`);
      const rows = await tx`SELECT id, date, source, summary FROM timeline_entries WHERE page_id = ${p.id} ORDER BY date, id`;
      const kv = rows.filter(r => JSON.stringify(r).toLowerCase().includes('kvzq'));
      console.log(`${t.slug} (page ${p.id}): ${rows.length} timeline_entries, kvzq ones: ${JSON.stringify(kv.map(r => ({ id: r.id, source: r.source, summary: r.summary })))}`);
      if (kv.length !== 1 || kv[0].id !== t.entry || kv[0].source !== SOURCE) throw new Error(`${t.slug}: expected exactly entry ${t.entry} from ${SOURCE}`);
      const del = await tx`DELETE FROM timeline_entries WHERE id = ${t.entry} AND page_id = ${p.id} AND source = ${SOURCE} RETURNING id`;
      if (del.length !== 1) throw new Error(`${t.slug}: delete removed ${del.length} rows`);
      const bs = blocks(p.timeline);
      const hit = bs.filter(b => b.includes(SOURCE));
      console.log(`${t.slug}: page timeline text ${Buffer.byteLength(p.timeline ?? '')} bytes in ${bs.length} blocks; blocks naming the meeting: ${hit.length}`);
      for (const h of hit) console.log(`  removing block: ${JSON.stringify(h)}`);
      if (hit.length > 1) throw new Error(`${t.slug}: more than one timeline block names the meeting`);
      if (hit.length === 1) {
        const next = bs.filter(b => !b.includes(SOURCE)).join('\n');
        const upd = await tx`UPDATE pages SET timeline = ${next} WHERE id = ${p.id} AND timeline = ${p.timeline} RETURNING id`;
        if (upd.length !== 1) throw new Error(`${t.slug}: timeline text changed underneath`);
      }
      const after = await tx`SELECT count(*)::int AS n FROM timeline_entries WHERE page_id = ${p.id}`;
      const [pa] = await tx`SELECT timeline, octet_length(compiled_truth) AS cbytes FROM pages WHERE id = ${p.id}`;
      console.log(`${t.slug}: after: ${after[0].n} timeline_entries (was ${rows.length}); timeline text mentions kvzq: ${(pa.timeline ?? '').toLowerCase().includes('kvzq')}; compiled_truth bytes ${p.cbytes} -> ${pa.cbytes} (unchanged: ${String(p.cbytes) === String(pa.cbytes)})`);
    }
  });
} catch (e) {
  ok = false;
  console.log(`ROLLED BACK: ${e.message}`);
}
try {
  const cols = await db`SELECT column_name FROM information_schema.columns WHERE table_name = 'content_chunks' ORDER BY ordinal_position`;
  console.log(`content_chunks columns: ${cols.map(c => c.column_name).join(', ')}`);
  const tc = cols.map(c => c.column_name).find(c => /text|content|chunk/.test(c) && c !== 'page_id');
  if (tc) {
    const ch = await db.unsafe(`SELECT p.slug, count(*)::int AS n FROM content_chunks c JOIN pages p ON p.id = c.page_id WHERE p.slug IN ('people/dylan-evans', 'companies/simple-salt') AND lower(c.${tc}::text) LIKE '%kvzq%' GROUP BY 1`);
    console.log(`content_chunks still mentioning kvzq (left as is): ${JSON.stringify(ch)}`);
  }
} catch (e) { console.log(`chunk check skipped: ${e.message}`); }
console.log(`RESULT: ${ok ? 'PASS' : 'FAIL'}`);
process.exit(ok ? 0 : 1);
