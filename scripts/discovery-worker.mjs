#!/usr/bin/env bun
import { SQL } from "bun";

const DATABASE_URL = process.env.DATABASE_URL;
const SERPER_API_KEY = process.env.SERPER_API_KEY;
const EXA_API_KEY = process.env.EXA_API_KEY;
const POLL_INTERVAL_MS = Number(process.env.POLL_INTERVAL_MS ?? 30_000);
const RESULTS_PER_QUERY = 20;
const PAGE_TEXT_MAX_CHARS = 8000;

const EXA_CONTENTS = {
  text: { maxCharacters: PAGE_TEXT_MAX_CHARS },
  highlights: {
    query: "Required skills and expertise, what success looks like in this role, and whether the role can be done remotely",
    maxCharacters: 1000,
  },
  summary: {
    query: "What special skills or expertise is desired? What does success look like for this role? Can the role be done remotely?",
    schema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      title: "RoleSummary",
      type: "object",
      properties: {
        skills: { type: "string", description: "Special skills or expertise the employer wants." },
        success: { type: "string", description: "What success looks like for this role." },
        remote_eligibility: {
          type: "string",
          enum: ["remote", "hybrid", "onsite", "unknown"],
          description: "remote if the role can be done fully remotely, hybrid if partly, onsite if not, unknown if the page does not say.",
        },
      },
      required: ["skills", "success", "remote_eligibility"],
    },
  },
};

if (!DATABASE_URL) throw new Error("DATABASE_URL is required");
if (!SERPER_API_KEY) throw new Error("SERPER_API_KEY is required");
if (!EXA_API_KEY) throw new Error("EXA_API_KEY is required");

const sql = new SQL(DATABASE_URL);

// Bump this to re-request every seed search on the next restart, even if it already ran.
const SEED_VERSION = 4;

const SEEDS = [
  {
    name: "karen",
    role: "Security Chief of Staff",
    company_type: "Fortune 500",
    responsibilities:
      "Project manager and fixer for security efforts; drives cross-functional execution for the CISO organization.",
    flavor:
      "Look for program-management language (roadmaps, OKRs, cross-team coordination) paired with security scope; filter out pure EA/admin roles with no security remit.",
    serper_queries: [],
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
    serper_queries: [],
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
    serper_queries: [],
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
    serper_queries: [],
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
    serper_queries: [],
    exa_queries: [
      "Job posting for a security developer evangelist or developer advocate role at a software company, engaging developers on security best practices",
    ],
  },
];

// Re-applied every tick: CNPG may create the crawl_read role after this worker starts.
const READ_ROLE_GRANTS = `DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crawl_read') THEN
      GRANT USAGE ON SCHEMA public TO crawl_read;
      GRANT SELECT ON ALL TABLES IN SCHEMA public TO crawl_read;
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO crawl_read;
    END IF;
  END $$`;

// Seed list of consulting firms whose roles are excluded. Rows live in
// excluded_employers so agents can add or remove firms; seeding never overwrites edits.
const CONSULTING_FIRMS = [
  { name: "Deloitte", domains: ["deloitte.com"], patterns: [String.raw`\mdeloitte\M`] },
  { name: "Accenture", domains: ["accenture.com"], patterns: [String.raw`\maccenture\M`] },
  { name: "PwC", domains: ["pwc.com"], patterns: [String.raw`\mpwc\M`, "pricewaterhouse"] },
  { name: "EY", domains: ["ey.com"], patterns: [String.raw`ernst\s*(&|and)\s*young`, String.raw`[./]ey\.com`, String.raw`/ey(/|$)`] },
  { name: "KPMG", domains: ["kpmg.com", "kpmg.us"], patterns: [String.raw`\mkpmg\M`] },
  { name: "McKinsey", domains: ["mckinsey.com"], patterns: [String.raw`\mmckinsey\M`] },
  { name: "Boston Consulting Group", domains: ["bcg.com"], patterns: ["boston consulting group", String.raw`\mbcg\M`] },
  { name: "Bain", domains: ["bain.com"], patterns: [String.raw`\mbain\s*(&|and)\s*company\M`] },
  { name: "Booz Allen Hamilton", domains: ["boozallen.com"], patterns: [String.raw`booz\s*allen`] },
  { name: "Capgemini", domains: ["capgemini.com"], patterns: [String.raw`\mcapgemini\M`] },
  { name: "Cognizant", domains: ["cognizant.com"], patterns: [String.raw`\mcognizant\M`] },
  { name: "Infosys", domains: ["infosys.com"], patterns: [String.raw`\minfosys\M`] },
  { name: "Wipro", domains: ["wipro.com"], patterns: [String.raw`\mwipro\M`] },
  { name: "Tata Consultancy Services", domains: ["tcs.com"], patterns: ["tata consultancy", String.raw`\mtcs\M`] },
  { name: "HCLTech", domains: ["hcltech.com"], patterns: [String.raw`\mhcl\s*tech`] },
  { name: "Tech Mahindra", domains: ["techmahindra.com"], patterns: [String.raw`tech\s*mahindra`] },
  { name: "Genpact", domains: ["genpact.com"], patterns: [String.raw`\mgenpact\M`] },
  { name: "DXC Technology", domains: ["dxc.com"], patterns: [String.raw`\mdxc\M`] },
  { name: "NTT DATA", domains: ["nttdata.com"], patterns: [String.raw`ntt\s*data`] },
  { name: "CGI", domains: ["cgi.com"], patterns: [String.raw`\mcgi\M`] },
  { name: "Protiviti", domains: ["protiviti.com"], patterns: [String.raw`\mprotiviti\M`] },
  { name: "Grant Thornton", domains: ["grantthornton.com"], patterns: [String.raw`grant\s*thornton`] },
  { name: "BDO", domains: ["bdo.com"], patterns: [String.raw`\mbdo\M`] },
  { name: "RSM", domains: ["rsmus.com"], patterns: [String.raw`\mrsm\s*(us|us llp)?\M`] },
  { name: "Crowe", domains: ["crowe.com"], patterns: [String.raw`\mcrowe\M`] },
  { name: "Guidehouse", domains: ["guidehouse.com"], patterns: [String.raw`\mguidehouse\M`] },
  { name: "Huron", domains: ["huronconsultinggroup.com"], patterns: [String.raw`huron\s*consulting`] },
  { name: "Kroll", domains: ["kroll.com"], patterns: [String.raw`\mkroll\M`] },
  { name: "Optiv", domains: ["optiv.com"], patterns: [String.raw`\moptiv\M`] },
  { name: "Coalfire", domains: ["coalfire.com"], patterns: [String.raw`\mcoalfire\M`] },
  { name: "Slalom", domains: ["slalom.com"], patterns: [String.raw`\mslalom\M`] },
  { name: "Avanade", domains: ["avanade.com"], patterns: [String.raw`\mavanade\M`] },
];

const TITLE_VERSION = 1;

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
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS skills text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS success text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS remote_eligibility text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS highlights text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS page_text text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS contents_fetched_at timestamptz`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS job_title text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS job_title_version int`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS excluded_employer text`,
  `CREATE TABLE IF NOT EXISTS excluded_employers (
    name text PRIMARY KEY,
    domains text[] NOT NULL DEFAULT '{}'::text[],
    patterns text[] NOT NULL DEFAULT '{}'::text[],
    note text,
    created_at timestamptz NOT NULL DEFAULT now()
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
  `COMMENT ON COLUMN candidates.skills IS 'Special skills or expertise the posting asks for, summarized by Exa.'`,
  `COMMENT ON COLUMN candidates.success IS 'What success looks like in the role, summarized by Exa.'`,
  `COMMENT ON COLUMN candidates.remote_eligibility IS 'Whether the role can be done remotely: remote, hybrid, onsite or unknown, judged by Exa from the page.'`,
  `COMMENT ON COLUMN candidates.highlights IS 'Most relevant sentences from the page about skills, success and remote work, joined with " … ".'`,
  `COMMENT ON COLUMN candidates.page_text IS 'Page text as fetched by Exa, capped in length.'`,
  `COMMENT ON COLUMN candidates.job_title IS 'The job title alone, cleaned from the page title (company, site, location, remote markers and requisition IDs removed).'`,
  `COMMENT ON COLUMN candidates.excluded_employer IS 'Name of the excluded employer (see excluded_employers) this posting matches; null if not excluded.'`,
  `COMMENT ON TABLE excluded_employers IS 'Employers whose roles are excluded from discovery results, such as consulting firms. Edit freely; the worker re-applies it every cycle.'`,
  `COMMENT ON COLUMN excluded_employers.domains IS 'The employer''s own web domains; Exa searches skip these and postings on them are flagged.'`,
  `COMMENT ON COLUMN excluded_employers.patterns IS 'Case-insensitive Postgres regular expressions matched against a posting''s URL and title.'`,
  `COMMENT ON COLUMN candidates.contents_fetched_at IS 'When the summary, highlights and page text were last refreshed.'`,
];

async function migrate() {
  for (const stmt of DDL_STATEMENTS) await sql.unsafe(stmt);
}

async function seed() {
  for (const f of CONSULTING_FIRMS) {
    await sql`
      INSERT INTO excluded_employers (name, domains, patterns, note)
      VALUES (
        ${f.name},
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(f.domains)}::text::jsonb)),
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(f.patterns)}::text::jsonb)),
        'consulting firm (seed)'
      )
      ON CONFLICT (name) DO NOTHING
    `;
  }
  for (const s of SEEDS) {
    await sql`
      INSERT INTO searches (
        name, role, company_type, responsibilities, flavor,
        serper_queries, exa_queries, seed_version, run_requested_at
      )
      VALUES (
        ${s.name}, ${s.role}, ${s.company_type}, ${s.responsibilities}, ${s.flavor},
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(s.serper_queries)}::text::jsonb)),
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(s.exa_queries)}::text::jsonb)), ${SEED_VERSION}, now()
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

const ROLE_WORDS =
  /\b(security|officer|architect|architecture|manager|director|engineer|analyst|evangelist|advocate|lead|chief|head|specialist|consultant|principal|staff|biso|ciso|cso|grc|governance|risk|compliance|program|vp|vice president|administrator|auditor|strategist|advisor|of staff)\b/i;

// Deterministic cleanup of a page title down to the job title alone.
function normalizeTitle(raw) {
  if (!raw) return null;
  let t = String(raw).replace(/\s+/g, " ").trim();
  t = t.replace(/^(job application for|apply(?: now)? (?:for|to)|careers?\s*[-:|]|jobs?\s*[-:|]|now hiring:?|hiring:?|we'?re hiring:?|opening:?)\s*/i, "");
  t = t.replace(/\s*[([][^)\]]*(remote|hybrid|on-?site|united states|usa)[^)\]]*[)\]]/gi, "");
  const parts = t.split(/\s+[|–—·•]\s+|\s+-\s+|\s*::\s*/).map((x) => x.trim()).filter(Boolean);
  let pick = parts.find((x) => ROLE_WORDS.test(x)) ?? parts[0] ?? t;
  const at = pick.match(/^(.*?\S)\s+(?:at|@)\s+[A-Z0-9].*$/);
  if (at && ROLE_WORDS.test(at[1])) pick = at[1];
  for (let i = 0; i < 3; i++) {
    pick = pick.replace(/\s*[([][^)\]]*[)\]]\s*$/, (m) =>
      /remote|hybrid|on-?site|\b[A-Z]{2}\b|,|\d|united states|usa/i.test(m) ? "" : m,
    );
  }
  pick = pick.replace(/[,\s]+(remote|hybrid|on-?site)\b.*$/i, "");
  pick = pick.replace(/\s*(#|req(?:uisition)?\s*(?:id)?\s*[:#]?|jr|r)\s*-?\d{3,}\s*$/i, "");
  pick = pick.replace(/\s*[-–,:|]+\s*$/, "").trim();
  return pick || String(raw).trim();
}

async function applyTitlesAndExclusions() {
  const rows = await sql`
    SELECT id, title FROM candidates
    WHERE job_title_version IS DISTINCT FROM ${TITLE_VERSION}
    LIMIT 500
  `;
  for (const row of rows) {
    await sql`
      UPDATE candidates SET job_title = ${normalizeTitle(row.title)}, job_title_version = ${TITLE_VERSION}
      WHERE id = ${row.id}
    `;
  }
  await sql`
    UPDATE candidates c SET excluded_employer = m.name
    FROM (
      SELECT c2.id, (
        SELECT e.name FROM excluded_employers e
        WHERE EXISTS (SELECT 1 FROM unnest(e.domains) d WHERE c2.domain = d OR c2.domain LIKE '%.' || d)
           OR EXISTS (SELECT 1 FROM unnest(e.patterns) p WHERE c2.canonical_url ~* p OR coalesce(c2.title, '') ~* p)
        ORDER BY e.name LIMIT 1
      ) AS name
      FROM candidates c2
    ) m
    WHERE c.id = m.id AND c.excluded_employer IS DISTINCT FROM m.name
  `;
}

async function excludedDomains() {
  const rows = await sql`SELECT DISTINCT unnest(domains) AS d FROM excluded_employers`;
  return rows.map((r) => r.d);
}

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

// Exa returns a schema summary as a JSON string; fall back to treating plain text as skills.
function parseSummary(raw) {
  if (!raw) return {};
  if (typeof raw === "object") return raw;
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" ? v : { skills: String(raw) };
  } catch {
    return { skills: String(raw) };
  }
}

function normalizeRemote(v) {
  const s = String(v ?? "").toLowerCase();
  return ["remote", "hybrid", "onsite"].includes(s) ? s : "unknown";
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
    body: JSON.stringify({
      query: q,
      numResults: RESULTS_PER_QUERY,
      type: "auto",
      contents: EXA_CONTENTS,
      excludeDomains: await excludedDomains(),
    }),
  });
  if (!r.ok) throw new Error(`exa ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const body = await r.json();
  const results = body.results ?? [];
  return results.map((o, i) => {
    const summary = parseSummary(o.summary);
    const { text, ...rawWithoutText } = o;
    return {
      url: o.url,
      title: o.title ?? null,
      snippet: null,
      publishedAt: parseDate(o.publishedDate),
      rank: i + 1,
      raw: rawWithoutText,
      contents: {
        skills: summary.skills ?? null,
        success: summary.success ?? null,
        remoteEligibility: normalizeRemote(summary.remote_eligibility),
        highlights: Array.isArray(o.highlights) && o.highlights.length ? o.highlights.join(" … ") : null,
        pageText: typeof text === "string" ? text.slice(0, PAGE_TEXT_MAX_CHARS) : null,
      },
    };
  });
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
      VALUES (${canonical}, ${r.url}, ${domain}, ${r.title}, ${r.snippet}, ${r.publishedAt}, ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify([source])}::text::jsonb)))
      ON CONFLICT (canonical_url) DO UPDATE SET
        last_seen_at = now(),
        times_seen = candidates.times_seen + 1,
        sources = (
          SELECT array_agg(DISTINCT s ORDER BY s)
          FROM unnest(candidates.sources || ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify([source])}::text::jsonb))) AS s
        ),
        title = COALESCE(candidates.title, EXCLUDED.title),
        snippet = COALESCE(candidates.snippet, EXCLUDED.snippet),
        published_at = COALESCE(candidates.published_at, EXCLUDED.published_at)
      RETURNING id, (xmax = 0) AS inserted
    `;
    if (candidate.inserted) inserted++;
    else updated++;

    if (r.contents) {
      const c = r.contents;
      await sql`
        UPDATE candidates SET
          skills = COALESCE(${c.skills}, skills),
          success = COALESCE(${c.success}, success),
          remote_eligibility = CASE WHEN ${c.remoteEligibility} = 'unknown' AND remote_eligibility IS NOT NULL
                                    THEN remote_eligibility ELSE ${c.remoteEligibility} END,
          highlights = COALESCE(${c.highlights}, highlights),
          page_text = COALESCE(${c.pageText}, page_text),
          contents_fetched_at = now()
        WHERE id = ${candidate.id}
      `;
    }

    await sql`
      INSERT INTO sightings (candidate_id, search_id, run_id, source, query, rank, raw)
      VALUES (${candidate.id}, ${search.id}, ${run.id}, ${source}, ${query}, ${r.rank}, ${JSON.stringify(r.raw)}::text::jsonb)
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
      error_detail: errors.length ? errors.map((e) => e.slice(0, 300)) : undefined,
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
  try {
    await sql.unsafe(READ_ROLE_GRANTS);
  } catch (err) {
    console.error(JSON.stringify({ at: new Date().toISOString(), event: "grant_error", error: err.message }));
  }
  try {
    await applyTitlesAndExclusions();
  } catch (err) {
    console.error(JSON.stringify({ at: new Date().toISOString(), event: "cleanup_error", error: err.message }));
  }
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

// Diagnostic: titles that appear on more than one canonical URL, to see why dedupe misses.
async function logDuplicateReport() {
  try {
    const [totals] = await sql`
      SELECT count(*)::int AS candidates,
             count(DISTINCT lower(title))::int AS distinct_titles,
             count(DISTINCT canonical_url)::int AS distinct_urls
      FROM candidates
    `;
    const groups = await sql`
      SELECT lower(coalesce(job_title, title)) AS title, count(*)::int AS n,
             array_agg(canonical_url ORDER BY canonical_url) AS urls
      FROM candidates
      GROUP BY lower(coalesce(job_title, title))
      HAVING count(*) > 1
      ORDER BY count(*) DESC
      LIMIT 15
    `;
    console.log(JSON.stringify({ at: new Date().toISOString(), event: "duplicate_report", ...totals, groups }));
  } catch (err) {
    console.error(JSON.stringify({ at: new Date().toISOString(), event: "duplicate_report_error", error: err.message }));
  }
}

async function main() {
  await migrate();
  await seed();
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "startup", seed_version: SEED_VERSION }));
  await applyTitlesAndExclusions();
  await logDuplicateReport();
  while (!shuttingDown) {
    await tick();
    if (shuttingDown) break;
    await sleep(POLL_INTERVAL_MS);
  }
  await sql.close();
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "shutdown" }));
}

await main();
