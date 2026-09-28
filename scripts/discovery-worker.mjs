#!/usr/bin/env bun
import { SQL } from "bun";

const DATABASE_URL = process.env.DATABASE_URL;
const SERPER_API_KEY = process.env.SERPER_API_KEY;
const EXA_API_KEY = process.env.EXA_API_KEY;
const POLL_INTERVAL_MS = Number(process.env.POLL_INTERVAL_MS ?? 30_000);
const RESULTS_PER_QUERY = 20;

if (!DATABASE_URL) throw new Error("DATABASE_URL is required");
if (!SERPER_API_KEY) throw new Error("SERPER_API_KEY is required");
if (!EXA_API_KEY) throw new Error("EXA_API_KEY is required");

const sql = new SQL(DATABASE_URL);

// Bump this to re-request every seed search on the next restart, even if it already ran.
const SEED_VERSION = 1;

const SEEDS = [
  {
    name: "karen",
    role: "Security Chief of Staff",
    company_type: "Fortune 500",
    responsibilities:
      "Project manager and fixer for security efforts; drives cross-functional execution for the CISO organization.",
    flavor:
      "Look for program-management language (roadmaps, OKRs, cross-team coordination) paired with security scope; filter out pure EA/admin roles with no security remit.",
    serper_queries: [
      '"Security Chief of Staff" OR "Chief of Staff, Security" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
      '"Chief of Staff" "CISO" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
    ],
    exa_queries: [
      "Job posting for a Security Chief of Staff who acts as a program manager and fixer for a large enterprise security organization",
    ],
  },
  {
    name: "terry",
    role: "Security Architect",
    company_type: "Fortune 500",
    responsibilities:
      "Pushes architecture standards out to business divisions; some internal evangelism to drive adoption.",
    flavor:
      "Favor postings that mention setting or governing standards across multiple divisions/BUs, not just building one system; evangelism/advocacy language is a good signal.",
    serper_queries: [
      '"Security Architect" "enterprise architecture" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
      '"Principal Security Architect" OR "Enterprise Security Architect" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
    ],
    exa_queries: [
      "Job posting for a Security Architect at a Fortune 500 company who defines security architecture standards and drives adoption across business divisions",
    ],
  },
  {
    name: "dwayne",
    role: "Business Information Security Officer (BISO)",
    company_type: "Fortune 500",
    responsibilities:
      "Embedded in one division; improves security metrics and posture for that division specifically.",
    flavor:
      "Prefer postings tied to a single business unit/division rather than an enterprise-wide CISO role; metrics/posture-improvement language is a good signal.",
    serper_queries: [
      '"Business Information Security Officer" OR "BISO" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
      '"BISO" division security metrics (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
    ],
    exa_queries: [
      "Job posting for a Business Information Security Officer (BISO) embedded in a division of a large enterprise, responsible for security risk metrics and posture",
    ],
  },
  {
    name: "chad",
    role: "GRC Manager",
    company_type: "Fortune 500",
    responsibilities: "Drives governance, risk, and compliance program delivery.",
    flavor:
      "Delivery/execution language (managing audits, controls, frameworks like SOX/ISO/NIST) is a stronger signal than pure policy-writing roles.",
    serper_queries: [
      '"GRC Manager" OR "Governance Risk and Compliance Manager" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
      '"Manager, GRC" OR "GRC Program Manager" security (site:boards.greenhouse.io OR site:jobs.lever.co OR site:myworkdayjobs.com OR site:jobs.ashbyhq.com)',
    ],
    exa_queries: [
      "Job posting for a Governance, Risk, and Compliance (GRC) Manager at a large enterprise, driving delivery of GRC programs and compliance initiatives",
    ],
  },
  {
    name: "mario",
    role: "Security / Developer Evangelist",
    company_type: "software company",
    responsibilities: "Evangelizes security practices to developers; represents security externally.",
    flavor:
      "Best fit is a hybrid dev-facing + security role (advocacy, conference talks, content); plain product-marketing or pure AppSec-engineer roles are weaker matches.",
    serper_queries: [
      '"Security Developer Advocate" OR "Developer Evangelist" security (site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com)',
      '"Security Evangelist" software (site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com)',
    ],
    exa_queries: [
      "Job posting for a security developer evangelist or developer advocate role at a software company, engaging developers on security best practices",
    ],
  },
];

const DDL_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS searches (
    id bigserial PRIMARY KEY,
    name text UNIQUE NOT NULL,
    role text,
    company_type text,
    responsibilities text,
    flavor text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    serper_queries text[] NOT NULL DEFAULT '{}'::text[],
    exa_queries text[] NOT NULL DEFAULT '{}'::text[],
    seed_version int,
    run_requested_at timestamptz,
    last_run_started_at timestamptz,
    last_run_finished_at timestamptz,
    last_run_status text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
  )`,
  `CREATE TABLE IF NOT EXISTS search_runs (
    id bigserial PRIMARY KEY,
    search_id bigint NOT NULL REFERENCES searches (id),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text,
    serper_results int,
    exa_results int,
    candidates_inserted int,
    candidates_updated int,
    error text
  )`,
  `CREATE INDEX IF NOT EXISTS search_runs_search_id_idx ON search_runs (search_id)`,
  `CREATE TABLE IF NOT EXISTS candidates (
    id bigserial PRIMARY KEY,
    canonical_url text UNIQUE NOT NULL,
    url text,
    domain text,
    title text,
    snippet text,
    published_at timestamptz,
    sources text[],
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    times_seen int NOT NULL DEFAULT 1
  )`,
  `CREATE TABLE IF NOT EXISTS sightings (
    id bigserial PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidates (id),
    search_id bigint NOT NULL REFERENCES searches (id),
    run_id bigint NOT NULL REFERENCES search_runs (id),
    source text NOT NULL,
    query text,
    rank int,
    raw jsonb,
    seen_at timestamptz NOT NULL DEFAULT now()
  )`,
  `CREATE INDEX IF NOT EXISTS sightings_candidate_id_idx ON sightings (candidate_id)`,
  `CREATE INDEX IF NOT EXISTS sightings_search_id_idx ON sightings (search_id)`,
  `CREATE INDEX IF NOT EXISTS sightings_run_id_idx ON sightings (run_id)`,
  `COMMENT ON TABLE searches IS 'A saved job-discovery profile: who we are looking for and the queries used to find them.'`,
  `COMMENT ON COLUMN searches.name IS 'Short label identifying this search profile (not shown to candidates).'`,
  `COMMENT ON COLUMN searches.role IS 'Target job title or role family this search looks for.'`,
  `COMMENT ON COLUMN searches.company_type IS 'Kind of employer this search targets, e.g. Fortune 500.'`,
  `COMMENT ON COLUMN searches.responsibilities IS 'What the target role is expected to do day to day.'`,
  `COMMENT ON COLUMN searches.flavor IS 'Free-text colour an agent uses to plan follow-up steps, such as how to filter or prioritize results.'`,
  `COMMENT ON COLUMN searches.attributes IS 'Open-ended extra profile attributes as JSON.'`,
  `COMMENT ON COLUMN searches.serper_queries IS 'Search-engine queries run against Serper for this profile.'`,
  `COMMENT ON COLUMN searches.exa_queries IS 'Natural-language queries run against Exa for this profile.'`,
  `COMMENT ON COLUMN searches.seed_version IS 'Version stamp from the seed data that last wrote this row; bumping it in git re-requests a run.'`,
  `COMMENT ON COLUMN searches.run_requested_at IS 'When a run was last requested; a worker claims the search when this is newer than last_run_started_at.'`,
  `COMMENT ON COLUMN searches.last_run_started_at IS 'When the most recent run started.'`,
  `COMMENT ON COLUMN searches.last_run_finished_at IS 'When the most recent run finished.'`,
  `COMMENT ON COLUMN searches.last_run_status IS 'Outcome of the most recent run: ok or error.'`,
  `COMMENT ON TABLE candidates IS 'A distinct job posting URL discovered by any search, deduplicated by canonical URL.'`,
  `COMMENT ON COLUMN candidates.canonical_url IS 'Normalized URL used to deduplicate postings seen across searches and sources.'`,
  `COMMENT ON COLUMN candidates.url IS 'The URL exactly as first seen, before normalization.'`,
  `COMMENT ON COLUMN candidates.domain IS 'Hostname the posting was found on.'`,
  `COMMENT ON COLUMN candidates.title IS 'Posting title as reported by the source.'`,
  `COMMENT ON COLUMN candidates.snippet IS 'Short excerpt or summary as reported by the source.'`,
  `COMMENT ON COLUMN candidates.published_at IS 'Publish date reported by the source, when available.'`,
  `COMMENT ON COLUMN candidates.sources IS 'Which search providers (serper, exa) have surfaced this URL.'`,
  `COMMENT ON COLUMN candidates.first_seen_at IS 'When this URL was first discovered.'`,
  `COMMENT ON COLUMN candidates.last_seen_at IS 'When this URL was most recently discovered again.'`,
  `COMMENT ON COLUMN candidates.times_seen IS 'How many times this URL has been discovered across all searches and runs.'`,
];

async function migrate() {
  for (const stmt of DDL_STATEMENTS) await sql.unsafe(stmt);
}

async function seed() {
  for (const s of SEEDS) {
    await sql`
      INSERT INTO searches (
        name, role, company_type, responsibilities, flavor,
        serper_queries, exa_queries, seed_version, run_requested_at
      )
      VALUES (
        ${s.name}, ${s.role}, ${s.company_type}, ${s.responsibilities}, ${s.flavor},
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(s.serper_queries)}::jsonb)),
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(s.exa_queries)}::jsonb)), ${SEED_VERSION}, now()
      )
      ON CONFLICT (name) DO UPDATE SET
        role = EXCLUDED.role,
        company_type = EXCLUDED.company_type,
        responsibilities = EXCLUDED.responsibilities,
        flavor = EXCLUDED.flavor,
        serper_queries = EXCLUDED.serper_queries,
        exa_queries = EXCLUDED.exa_queries,
        seed_version = EXCLUDED.seed_version,
        run_requested_at = now(),
        updated_at = now()
      WHERE searches.seed_version IS DISTINCT FROM EXCLUDED.seed_version
    `;
  }
}

// Params dropped regardless of key case: exact matches plus any utm_* prefix.
const DROP_PARAMS = new Set(["gclid", "fbclid", "gh_src", "source", "ref", "lever-source", "lever-origin"]);

function canonicalizeUrl(raw) {
  if (!raw) return null;
  let u;
  try {
    u = new URL(raw);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;

  let host = u.hostname.toLowerCase();
  if (host.startsWith("www.")) host = host.slice(4);

  for (const key of [...u.searchParams.keys()]) {
    const lower = key.toLowerCase();
    if (lower.startsWith("utm_") || DROP_PARAMS.has(lower)) u.searchParams.delete(key);
  }
  u.searchParams.sort();

  let path = u.pathname;
  if (path.endsWith("/")) path = path.slice(0, -1);

  const qs = u.searchParams.toString();
  return `${u.protocol}//${host}${path}${qs ? `?${qs}` : ""}`;
}

function parseDate(v) {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

async function serperSearch(q) {
  const r = await fetch("https://google.serper.dev/search", {
    method: "POST",
    headers: { "X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({ q, num: RESULTS_PER_QUERY }),
  });
  if (!r.ok) throw new Error(`serper ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const body = await r.json();
  const organic = body.organic ?? [];
  return organic.map((o, i) => ({
    url: o.link,
    title: o.title ?? null,
    snippet: o.snippet ?? null,
    publishedAt: parseDate(o.date),
    rank: i + 1,
    raw: o,
  }));
}

async function exaSearch(q) {
  const r = await fetch("https://api.exa.ai/search", {
    method: "POST",
    headers: { "x-api-key": EXA_API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({ query: q, numResults: RESULTS_PER_QUERY, type: "auto" }),
  });
  if (!r.ok) throw new Error(`exa ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const body = await r.json();
  const results = body.results ?? [];
  return results.map((o, i) => ({
    url: o.url,
    title: o.title ?? null,
    snippet: null,
    publishedAt: parseDate(o.publishedDate),
    rank: i + 1,
    raw: o,
  }));
}

async function storeResults({ search, run, source, query, results }) {
  let inserted = 0;
  let updated = 0;
  for (const r of results) {
    const canonical = canonicalizeUrl(r.url);
    if (!canonical) continue;
    const domain = new URL(canonical).hostname;

    const [candidate] = await sql`
      INSERT INTO candidates (canonical_url, url, domain, title, snippet, published_at, sources)
      VALUES (${canonical}, ${r.url}, ${domain}, ${r.title}, ${r.snippet}, ${r.publishedAt}, ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify([source])}::jsonb)))
      ON CONFLICT (canonical_url) DO UPDATE SET
        last_seen_at = now(),
        times_seen = candidates.times_seen + 1,
        sources = (
          SELECT array_agg(DISTINCT s ORDER BY s)
          FROM unnest(candidates.sources || ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify([source])}::jsonb))) AS s
        ),
        title = COALESCE(candidates.title, EXCLUDED.title),
        snippet = COALESCE(candidates.snippet, EXCLUDED.snippet),
        published_at = COALESCE(candidates.published_at, EXCLUDED.published_at)
      RETURNING *, (xmax = 0) AS inserted
    `;
    if (candidate.inserted) inserted++;
    else updated++;

    await sql`
      INSERT INTO sightings (candidate_id, search_id, run_id, source, query, rank, raw)
      VALUES (${candidate.id}, ${search.id}, ${run.id}, ${source}, ${query}, ${r.rank}, ${JSON.stringify(r.raw)}::jsonb)
    `;
  }
  return { inserted, updated };
}

async function claimSearch() {
  return await sql.begin(async (tx) => {
    const [search] = await tx`
      SELECT id, name,
        to_jsonb(serper_queries) AS serper_queries,
        to_jsonb(exa_queries) AS exa_queries
      FROM searches
      WHERE run_requested_at IS NOT NULL
        AND (last_run_started_at IS NULL OR run_requested_at > last_run_started_at)
      ORDER BY run_requested_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    `;
    if (!search) return null;
    await tx`UPDATE searches SET last_run_started_at = now(), updated_at = now() WHERE id = ${search.id}`;
    const [run] = await tx`
      INSERT INTO search_runs (search_id, started_at, status)
      VALUES (${search.id}, now(), 'running')
      RETURNING *
    `;
    return { search, run };
  });
}

async function runSearch(search, run) {
  const counts = { serper: 0, exa: 0, inserted: 0, updated: 0 };
  const errors = [];

  for (const q of search.serper_queries ?? []) {
    try {
      const results = await serperSearch(q);
      counts.serper += results.length;
      const r = await storeResults({ search, run, source: "serper", query: q, results });
      counts.inserted += r.inserted;
      counts.updated += r.updated;
    } catch (err) {
      errors.push(`serper[${q}]: ${err.message}`);
    }
  }

  for (const q of search.exa_queries ?? []) {
    try {
      const results = await exaSearch(q);
      counts.exa += results.length;
      const r = await storeResults({ search, run, source: "exa", query: q, results });
      counts.inserted += r.inserted;
      counts.updated += r.updated;
    } catch (err) {
      errors.push(`exa[${q}]: ${err.message}`);
    }
  }

  const status = errors.length ? "error" : "ok";
  const errorText = errors.length ? errors.join(" | ") : null;

  await sql`
    UPDATE search_runs SET
      finished_at = now(),
      status = ${status},
      serper_results = ${counts.serper},
      exa_results = ${counts.exa},
      candidates_inserted = ${counts.inserted},
      candidates_updated = ${counts.updated},
      error = ${errorText}
    WHERE id = ${run.id}
  `;
  await sql`
    UPDATE searches SET
      last_run_finished_at = now(),
      last_run_status = ${status},
      updated_at = now()
    WHERE id = ${search.id}
  `;

  console.log(
    JSON.stringify({
      at: new Date().toISOString(),
      search: search.name,
      serper_results: counts.serper,
      exa_results: counts.exa,
      inserted: counts.inserted,
      updated: counts.updated,
      errors: errors.length,
    }),
  );
}

let shuttingDown = false;
let pendingResolve = null;

function sleep(ms) {
  return new Promise((resolve) => {
    pendingResolve = resolve;
    setTimeout(resolve, ms);
  });
}

function shutdown() {
  shuttingDown = true;
  pendingResolve?.();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

async function tick() {
  let claimed;
  try {
    claimed = await claimSearch();
  } catch (err) {
    console.error(JSON.stringify({ at: new Date().toISOString(), event: "claim_error", error: err.message }));
    return;
  }
  if (!claimed) return;
  try {
    await runSearch(claimed.search, claimed.run);
  } catch (err) {
    console.error(
      JSON.stringify({
        at: new Date().toISOString(),
        event: "run_error",
        search: claimed.search.name,
        error: err.message,
      }),
    );
  }
}

async function main() {
  await migrate();
  await seed();
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "startup", seed_version: SEED_VERSION }));
  while (!shuttingDown) {
    await tick();
    if (shuttingDown) break;
    await sleep(POLL_INTERVAL_MS);
  }
  await sql.close();
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "shutdown" }));
}

await main();
