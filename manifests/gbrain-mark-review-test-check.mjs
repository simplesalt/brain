import { SQL } from 'bun';
const db = new SQL(process.env.DATABASE_URL);
const slugs = JSON.parse(await Bun.file('/work/slugs.json').text());
for (const s of slugs) {
  const [p] = await db`SELECT slug, deleted_at::text AS deleted_at, left(compiled_truth, 60) AS body FROM pages WHERE slug = ${s} AND source_id = 'default'`;
  const [m] = await db`SELECT kind, outcome, applied_at::text AS applied_at, withdrawn_at::text AS withdrawn_at FROM addon_page_marks WHERE slug = ${s} ORDER BY id DESC LIMIT 1`;
  console.log(JSON.stringify({ slug: s, page: p ?? null, mark: m }));
}
await db.end();
console.log('RESULT: checked');
