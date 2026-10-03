#!/usr/bin/env bun
import { SQL } from "bun";

const DATABASE_URL = process.env.DATABASE_URL;
const SERPER_API_KEY = process.env.SERPER_API_KEY;
const EXA_API_KEY = process.env.EXA_API_KEY;
const SERPAPI_KEY_FILE = process.env.SERPAPI_KEY_FILE ?? "/secrets/serpapi/api_key";
// Guard on every drain-until-empty loop in a cycle; hitting it is logged as loop_cap_hit.
const MAX_LOOP_ITERATIONS = 200;
const RESULTS_PER_QUERY = 20;

// A detail that could not be found holds one of these in the field itself: NOT_MENTIONED when the
// page does not state it, or `${CANT_FIND}: <reason>` when fetching or extraction failed (with
// "; max retries hit" appended once a temporary failure used up its tries). Every reader treats
// both exactly like a blank (see found() here and found_detail() in SQL).
const NOT_MENTIONED = "not mentioned";
const CANT_FIND = "can't find";
const MAX_RETRIES_NOTE = "max retries hit";

// A posting's details are tried this many times when the failure is temporary: the first try, one
// retry RETRY_DELAY_MS later in the same cycle, and one more try in the next cycle.
const MAX_DETAIL_TRIES = 3;
const RETRY_DELAY_MS = (() => {
  const n = Number(process.env.RETRY_DELAY_MS ?? 15 * 60 * 1000);
  return Number.isFinite(n) && n >= 0 ? n : 15 * 60 * 1000;
})();

// The detail's trimmed value, or null when it is missing: null, blank, 'unknown' or a marker.
// Mirrors the found_detail() SQL function.
function found(v) {
  if (v === null || v === undefined) return null;
  const s = String(v);
  if (!s.trim() || s === "unknown" || s === NOT_MENTIONED || s.startsWith(CANT_FIND)) return null;
  return s.trim();
}

// Read on every call: the Secret is mounted as a file, so a kubectl patch takes effect without a restart.
async function serpapiKey() {
  try {
    const v = (await Bun.file(SERPAPI_KEY_FILE).text()).trim();
    return v || null;
  } catch {
    return null;
  }
}

// Google Jobs queries per seed profile, run through SerpApi. Only filled in where a search has none,
// so agent edits are kept.
const GOOGLE_JOBS_SEEDS = {
  karen: ["security chief of staff remote", "chief of staff information security remote"],
  terry: ["enterprise security architect remote", "principal security architect remote"],
  dwayne: ["business information security officer remote", "BISO remote"],
  chad: ["GRC manager remote", "governance risk and compliance manager remote"],
  mario: ["security developer advocate remote", "security evangelist remote"],
};
const PAGE_TEXT_MAX_CHARS = 8000;

const EXA_CONTENTS = {
  text: { maxCharacters: PAGE_TEXT_MAX_CHARS },
};

// Fields our own model extracts from page text (Exa is only used for search and page text).
const ROLE_SCHEMA = {
      $schema: "http://json-schema.org/draft-07/schema#",
      title: "RoleSummary",
      type: "object",
      properties: {
        employer: {
          type: "string",
          description: "Name of the company that will employ the hire, as named in the job description. Never the job board, aggregator or recruiting site hosting the listing (for example not Jobgether, Jobsy, Jobtrail, Hiring Camp, Built In, The Muse, Dice or Simplify). Empty if the page does not name the employer.",
        },
        skills: {
          type: "array",
          items: { type: "string" },
          description: "Special skills or expertise wanted, as short keyword phrases.",
        },
        success: {
          type: "array",
          items: { type: "string" },
          description: "What success looks like in the role, as short keyword phrases.",
        },
        employer_sector: {
          type: "string",
          enum: ["security", "technology", "other", "unknown"],
          description: "security if the employer is a cybersecurity company, technology if it is a software, internet or IT company, other for any other industry (banks, retailers, manufacturers, healthcare, government), unknown if you cannot tell.",
        },
        remote_eligibility: {
          type: "string",
          enum: ["remote", "hybrid", "onsite", "unknown"],
          description: "remote if the role can be done fully remotely, hybrid if partly, onsite if not, unknown if the page does not say.",
        },
        posted_date: {
          type: "string",
          description: "Date the job was posted, as YYYY-MM-DD, resolving relative dates like '3 weeks ago' against today. Empty if not stated.",
        },
      },
      required: ["employer", "employer_sector", "skills", "success", "remote_eligibility", "posted_date"],
    };

if (!DATABASE_URL) throw new Error("DATABASE_URL is required");
if (!SERPER_API_KEY) throw new Error("SERPER_API_KEY is required");
if (!EXA_API_KEY) throw new Error("EXA_API_KEY is required");

const sql = new SQL(DATABASE_URL);

// Bump this to overwrite the seed searches' definitions on the next run. Every cycle already
// requests every search, so it no longer controls whether a search runs.
const SEED_VERSION = 5;

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

// Applied once per cycle: CNPG may create the crawl_read role after the previous cycle ended.
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

// Employers Dylan ruled out (security and tech companies, plus named firms). Seeded once, editable in the table.
const USER_EXCLUDED = [
  { name: "beBee", domains: ["bebee.com"], patterns: [String.raw`\mbebee\M`], category: "job board" },
  { name: "RunReveal", domains: ["runreveal.com"], patterns: [String.raw`\mrunreveal\M`], category: "tech/security" },
  { name: "GitHub", domains: ["github.com", "github.careers"], patterns: [String.raw`^github(,? inc\.?)?$`, String.raw`github\.careers`], category: "tech/security" },
  { name: "GitLab", domains: ["gitlab.com"], patterns: [String.raw`\mgitlab\M`], category: "tech/security" },
  { name: "Wiz", domains: ["wiz.io"], patterns: [String.raw`^wiz(,? inc\.?)?$`, String.raw`[./]wiz\.io`], category: "tech/security" },
  { name: "Checkmarx", domains: ["checkmarx.com"], patterns: [String.raw`\mcheckmarx\M`], category: "tech/security" },
  { name: "Standard Chartered", domains: ["sc.com"], patterns: [String.raw`standard\s*chartered`], category: "user excluded" },
  { name: "Vouched", domains: ["vouched.id"], patterns: [String.raw`^vouched`], category: "tech/security" },
  { name: "Trail of Bits", domains: ["trailofbits.com"], patterns: [String.raw`trail\s*of\s*bits`], category: "tech/security" },
  { name: "ServiceNow", domains: ["servicenow.com"], patterns: [String.raw`\mservicenow\M`], category: "tech/security" },
  { name: "Meta", domains: ["meta.com", "metacareers.com"], patterns: [String.raw`^meta( platforms)?(,? inc\.?)?$`, String.raw`metacareers\.com`], category: "tech/security" },
  { name: "Nike India", domains: [], patterns: [String.raw`nike\s*india`], category: "user excluded" },
];

const TITLE_VERSION = 3;

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
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS employer text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS role_id bigint`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS is_role_primary boolean`,
  `CREATE INDEX IF NOT EXISTS candidates_role_id_idx ON candidates (role_id)`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS enrich_attempts int NOT NULL DEFAULT 0`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS enriched_at timestamptz`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS primary_source_url text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS found_via bigint REFERENCES candidates (id)`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS posted_at timestamptz`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS employer_sector text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS is_job_posting boolean`,
  `ALTER TABLE searches ADD COLUMN IF NOT EXISTS google_jobs_queries text[] NOT NULL DEFAULT '{}'::text[]`,
  `ALTER TABLE searches ADD COLUMN IF NOT EXISTS google_jobs_run_at timestamptz`,
  `ALTER TABLE search_runs ADD COLUMN IF NOT EXISTS google_jobs_results int`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS live_status text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS live_checked_at timestamptz`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS live_detail text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS extract_version int`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS extract_model text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS extracted_at timestamptz`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS detail_attempts int NOT NULL DEFAULT 0`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS detail_pending boolean NOT NULL DEFAULT false`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS detail_error text`,
  `ALTER TABLE candidates ADD COLUMN IF NOT EXISTS detail_tried_at timestamptz`,
  `CREATE TABLE IF NOT EXISTS excluded_employers (
    name text PRIMARY KEY,
    domains text[] NOT NULL DEFAULT '{}'::text[],
    patterns text[] NOT NULL DEFAULT '{}'::text[],
    note text,
    category text,
    created_at timestamptz NOT NULL DEFAULT now()
  )`,
  `ALTER TABLE excluded_employers ADD COLUMN IF NOT EXISTS category text`,
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
  `COMMENT ON COLUMN candidates.skills IS 'Special skills or expertise the posting asks for, summarized by Exa. "not mentioned" means the page does not state it; "can''t find: <reason>" means fetching or extraction failed; both count as blank.'`,
  `COMMENT ON COLUMN candidates.success IS 'What success looks like in the role, summarized by Exa. "not mentioned" means the page does not state it; "can''t find: <reason>" means fetching or extraction failed; both count as blank.'`,
  `COMMENT ON COLUMN candidates.remote_eligibility IS 'Whether the role can be done remotely: remote, hybrid, onsite or unknown, judged by Exa from the page. "not mentioned" or "can''t find: <reason>" (fetching or extraction failed) count as unknown.'`,
  `COMMENT ON COLUMN candidates.highlights IS 'Most relevant sentences from the page about skills, success and remote work, joined with " … ".'`,
  `COMMENT ON COLUMN candidates.page_text IS 'Page text as fetched by Exa, capped in length.'`,
  `COMMENT ON COLUMN candidates.job_title IS 'The job title alone, cleaned from the page title (company, site, location, remote markers and requisition IDs removed). "not mentioned" or "can''t find: <reason>" count as blank.'`,
  `COMMENT ON COLUMN candidates.employer_sector IS 'Kind of company the employer is: security, technology or other, judged from the page. "not mentioned" or "can''t find: <reason>" count as blank.'`,
  `COMMENT ON COLUMN candidates.excluded_employer IS 'Name of the excluded employer (see excluded_employers) this posting matches; null if not excluded.'`,
  `COMMENT ON COLUMN candidates.employer IS 'Hiring company as named by Exa from the page (not the job board); used to match reposts of the same role. "not mentioned" means the page does not name it; "can''t find: <reason>" means fetching or extraction failed; both count as blank and never merge roles.'`,
  `COMMENT ON COLUMN candidates.role_id IS 'Identifies the real role: every repost of the same job on different sites shares one role_id.'`,
  `COMMENT ON COLUMN candidates.is_role_primary IS 'True for the one posting chosen to represent its role, preferring the employer''s own or ATS page.'`,
  `CREATE OR REPLACE FUNCTION found_detail(v text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN v IS NULL OR btrim(v) = '' OR v IN ('unknown', 'not mentioned') OR v LIKE 'can''t find%' THEN NULL ELSE v END $$`,
  `COMMENT ON FUNCTION found_detail(text) IS 'The detail itself when it was found, or null when it is missing: null, blank, unknown, "not mentioned" (the page does not state it) or "can''t find: <reason>" (fetching or extraction failed). Every filter and merge rule reads details through this.'`,
  // One-time: CREATE OR REPLACE cannot drop view columns, and the old view depends on the column
  // dropped below, so a view that still has applicants goes first; READ_ROLE_GRANTS re-grants it
  // right after migrate(). Later cycles replace the view in place, so readers never see it missing.
  `DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'roles' AND column_name = 'applicants') THEN
      DROP VIEW roles;
    END IF;
  END $$`,
  `ALTER TABLE candidates DROP COLUMN IF EXISTS applicants`,
  // Filters read found details only. Displayed details prefer a real value from any posting of the
  // role (the primary first) and fall back to the primary's own value, so a marker shows only when
  // no posting has anything real.
  `CREATE OR REPLACE VIEW roles AS
    SELECT c.role_id,
           coalesce(found_detail(c.job_title),
                    (SELECT found_detail(o.job_title) FROM candidates o WHERE o.role_id = c.role_id AND found_detail(o.job_title) IS NOT NULL ORDER BY o.id LIMIT 1),
                    c.job_title) AS job_title,
           coalesce(found_detail(c.employer), (SELECT max(found_detail(o.employer)) FROM candidates o WHERE o.role_id = c.role_id), c.employer) AS employer,
           coalesce((SELECT found_detail(o.remote_eligibility) FROM candidates o WHERE o.role_id = c.role_id AND found_detail(o.remote_eligibility) IS NOT NULL
                      ORDER BY o.is_role_primary DESC LIMIT 1),
                    nullif(c.remote_eligibility, 'unknown')) AS remote_eligibility,
           c.canonical_url AS url,
           c.domain,
           coalesce(found_detail(c.skills),
                    (SELECT found_detail(o.skills) FROM candidates o WHERE o.role_id = c.role_id AND found_detail(o.skills) IS NOT NULL LIMIT 1),
                    c.skills) AS skills,
           coalesce(found_detail(c.success),
                    (SELECT found_detail(o.success) FROM candidates o WHERE o.role_id = c.role_id AND found_detail(o.success) IS NOT NULL LIMIT 1),
                    c.success) AS success,
           coalesce(c.highlights, (SELECT o.highlights FROM candidates o WHERE o.role_id = c.role_id AND o.highlights IS NOT NULL LIMIT 1)) AS highlights,
           (SELECT min(o.published_at) FROM candidates o WHERE o.role_id = c.role_id) AS published_at,
           (SELECT max(o.last_seen_at) FROM candidates o WHERE o.role_id = c.role_id) AS last_seen_at,
           (SELECT count(*) FROM candidates o WHERE o.role_id = c.role_id)::int AS postings,
           (SELECT string_agg(DISTINCT s.name, ', ') FROM candidates o
              JOIN sightings g ON g.candidate_id = o.id JOIN searches s ON s.id = g.search_id
             WHERE o.role_id = c.role_id) AS profiles,
           (SELECT max(o.excluded_employer) FROM candidates o WHERE o.role_id = c.role_id) AS excluded_employer,
           (SELECT coalesce(min(o.posted_at), min(o.published_at), min(o.first_seen_at)) FROM candidates o WHERE o.role_id = c.role_id) AS posted_at,
           (a.excluded IS NULL AND a.remote = 'remote' AND a.posted >= now() - interval '1 month'
            AND a.is_job AND a.live <> 'closed' AND NOT a.tech AND NOT a.extract_failed AND a.has_detail) AS considered,
           coalesce((SELECT max(found_detail(o.employer_sector)) FROM candidates o WHERE o.role_id = c.role_id), c.employer_sector) AS employer_sector,
           coalesce(c.is_job_posting, true) AS is_job_posting,
           c.live_status,
           CASE
             WHEN a.excluded IS NOT NULL THEN 'excluded employer: ' || a.excluded
             WHEN a.tech THEN 'security or tech company'
             WHEN NOT a.is_job THEN 'not a job posting'
             WHEN a.live = 'closed' THEN 'posting closed'
             WHEN a.remote <> 'remote' THEN 'not remote (' || a.remote || ')'
             WHEN a.posted < now() - interval '1 month' THEN 'posted over a month ago'
             WHEN a.extract_failed THEN 'details could not be extracted'
             WHEN NOT a.has_detail THEN 'no skills or success found'
           END AS not_considered_reason
    FROM candidates c
    CROSS JOIN LATERAL (
      SELECT
        (SELECT max(o.excluded_employer) FROM candidates o WHERE o.role_id = c.role_id) AS excluded,
        coalesce((SELECT found_detail(o.remote_eligibility) FROM candidates o WHERE o.role_id = c.role_id AND found_detail(o.remote_eligibility) IS NOT NULL
                  ORDER BY o.is_role_primary DESC LIMIT 1), 'unknown') AS remote,
        (SELECT coalesce(min(o.posted_at), min(o.published_at), min(o.first_seen_at)) FROM candidates o WHERE o.role_id = c.role_id) AS posted,
        coalesce(c.is_job_posting, true) AS is_job,
        coalesce(c.live_status, 'unknown') AS live,
        EXISTS (SELECT 1 FROM candidates o WHERE o.role_id = c.role_id AND o.employer_sector IN ('security', 'technology')) AS tech,
        (c.extract_version IS NOT NULL AND c.extract_model IS NULL) AS extract_failed,
        (c.extract_version IS NULL
         OR EXISTS (SELECT 1 FROM candidates o WHERE o.role_id = c.role_id
                    AND (found_detail(o.skills) IS NOT NULL OR found_detail(o.success) IS NOT NULL))) AS has_detail
    ) a
    WHERE c.is_role_primary`,
  `COMMENT ON COLUMN candidates.posted_at IS 'Date the job was posted, as stated on the page.'`,
  `COMMENT ON VIEW roles IS 'One row per real role: reposts across job sites are merged, showing the best posting and the details gathered from all copies. considered is true when the role is fully remote, was posted within the last month, is a real job posting that is still open, is not at a security or technology company, is not at an excluded employer, and had its details extracted. not_considered_reason gives the first rule a role failed. A detail no posting of the role could supply shows as "not mentioned" (the page does not state it) or "can''t find: <reason>" (fetching or extraction failed); both count as missing in every rule.'`,
  `COMMENT ON COLUMN candidates.enriched_at IS 'When the enrichment stage last fetched this page directly to fill in employer and details and look for the primary source.'`,
  `COMMENT ON COLUMN candidates.primary_source_url IS 'Link from this page to the employer''s own or ATS posting, when one was found.'`,
  `COMMENT ON COLUMN candidates.found_via IS 'For postings discovered by following a link, the candidate whose page linked to it.'`,
  `COMMENT ON COLUMN searches.google_jobs_queries IS 'Queries run against Google for Jobs (via SerpApi) for this profile; one page of about 10 jobs each.'`,
  `COMMENT ON COLUMN searches.google_jobs_run_at IS 'When this search last ran its Google Jobs queries.'`,
  `COMMENT ON COLUMN candidates.live_status IS 'live, closed (page gone or says the job is closed/filled) or unknown (site blocked the check); from a plain page fetch, rechecked daily.'`,
  `COMMENT ON TABLE excluded_employers IS 'Employers whose roles are excluded from discovery results, such as consulting firms. Edit freely; the worker re-applies it every cycle.'`,
  `COMMENT ON COLUMN excluded_employers.domains IS 'The employer''s own web domains; Exa searches skip these and postings on them are flagged.'`,
  `COMMENT ON COLUMN excluded_employers.patterns IS 'Case-insensitive Postgres regular expressions matched against a posting''s URL and title.'`,
  `COMMENT ON COLUMN candidates.contents_fetched_at IS 'When the summary, highlights and page text were last refreshed.'`,
  `COMMENT ON COLUMN candidates.detail_attempts IS 'Temporary failures so far while filling this posting''s details (fetching its page or extracting from it). After 3, the missing details get "can''t find: <reason>; max retries hit" and no more tries are made.'`,
  `COMMENT ON COLUMN candidates.detail_pending IS 'True while a temporary failure awaits a retry: one 15 minutes later in the same cycle, then one in the next cycle.'`,
  `COMMENT ON COLUMN candidates.detail_error IS 'Why the last attempt to fill this posting''s details failed (the reason written after "can''t find: "); null once an attempt succeeds.'`,
  `COMMENT ON COLUMN candidates.detail_tried_at IS 'When the worker last tried to fill this posting''s details.'`,
];

async function migrate() {
  for (const stmt of DDL_STATEMENTS) await sql.unsafe(stmt);
}

async function seed() {
  const firms = [
    ...CONSULTING_FIRMS.map((f) => ({ ...f, category: "consulting" })),
    ...USER_EXCLUDED,
  ];
  for (const f of firms) {
    await sql`
      INSERT INTO excluded_employers (name, domains, patterns, note, category)
      VALUES (
        ${f.name},
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(f.domains)}::text::jsonb)),
        ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(f.patterns)}::text::jsonb)),
        'seed',
        ${f.category}
      )
      ON CONFLICT (name) DO NOTHING
    `;
  }
  await sql`UPDATE excluded_employers SET category = 'consulting' WHERE category IS NULL`;
  for (const [name, queries] of Object.entries(GOOGLE_JOBS_SEEDS)) {
    await sql`
      UPDATE searches
      SET google_jobs_queries = ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(queries)}::text::jsonb))
      WHERE name = ${name} AND google_jobs_queries = '{}'::text[]
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
  t = t.replace(/^(job application for|applying to|apply(?: now)? (?:for|to)|careers?\s*[-:|]|jobs?\s*[-:|]|now hiring:?|hiring:?|we'?re hiring:?|opening:?)\s*/i, "");
  t = t.replace(/\s+job details\b/i, "").replace(/\s+job$/i, "");
  t = t.replace(/,?\s*[$£€]\s?\d[\d,.]*k?(\s*[-–]\s*[$£€]?\s?\d[\d,.]*k?)?(\s*(per|\/)\s*(year|yr|hour|hr))?/gi, "");
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
  pick = pick.replace(/\s+in\s+(the\s+)?([Uu]nited [Ss]tates|USA|US|UK|Canada|India|[A-Z][a-z]+(,\s*[A-Z]{2})?)$/, "");
  pick = pick.replace(/\s*(#|req(?:uisition)?\s*(?:id)?\s*[:#]?|jr|r)\s*-?\d{3,}\s*$/i, "");
  pick = pick.replace(/\s*[-–,:|]+\s*$/, "").trim();
  return pick || String(raw).trim();
}

async function applyTitlesAndExclusions() {
  // Placeholder text from a model is blanked, but the "not mentioned" marker is deliberate and stays.
  await sql`
    UPDATE candidates SET
      skills = CASE WHEN skills ~* '^\\s*(•\\s*)?(n/?a|none|unknown|not (specified|stated|mentioned|provided))' AND skills <> ${NOT_MENTIONED} THEN NULL ELSE skills END,
      success = CASE WHEN success ~* '^\\s*(•\\s*)?(n/?a|none|unknown|not (specified|stated|mentioned|provided))' AND success <> ${NOT_MENTIONED} THEN NULL ELSE success END
    WHERE (skills ~* '^\\s*(•\\s*)?(n/?a|none|unknown|not (specified|stated|mentioned|provided))' AND skills <> ${NOT_MENTIONED})
       OR (success ~* '^\\s*(•\\s*)?(n/?a|none|unknown|not (specified|stated|mentioned|provided))' AND success <> ${NOT_MENTIONED})
  `;
  // Only a real-looking name can be an aggregator to blank; a marker is not a name and stays.
  const named = await sql`SELECT id, employer FROM candidates WHERE employer IS NOT NULL`;
  for (const r of named) {
    if (found(r.employer) !== null && cleanEmployer(r.employer) === null) {
      await sql`UPDATE candidates SET employer = NULL, enrich_attempts = LEAST(enrich_attempts, 1) WHERE id = ${r.id}`;
    }
  }
  const rows = await sql`
    SELECT id, title FROM candidates
    WHERE job_title_version IS DISTINCT FROM ${TITLE_VERSION}
    LIMIT 500
  `;
  for (const row of rows) {
    const jobTitle = normalizeTitle(row.title);
    // A marker job_title is never overwritten by a blank; a real cleaned title replaces it.
    await sql`
      UPDATE candidates SET
        job_title = CASE WHEN ${jobTitle}::text IS NULL AND (job_title = ${NOT_MENTIONED} OR job_title LIKE ${`${CANT_FIND}%`})
                         THEN job_title ELSE ${jobTitle}::text END,
        job_title_version = ${TITLE_VERSION},
        is_job_posting = ${ROLE_WORDS.test(jobTitle ?? "")}
      WHERE id = ${row.id}
    `;
  }
  await sql`
    UPDATE candidates c SET excluded_employer = m.name
    FROM (
      SELECT c2.id, (
        SELECT e.name FROM excluded_employers e
        WHERE EXISTS (SELECT 1 FROM unnest(e.domains) d WHERE c2.domain = d OR c2.domain LIKE '%.' || d)
           OR EXISTS (SELECT 1 FROM unnest(e.patterns) p WHERE c2.canonical_url ~* p OR coalesce(c2.title, '') ~* p OR coalesce(found_detail(c2.employer), '') ~* p)
        ORDER BY e.name LIMIT 1
      ) AS name
      FROM candidates c2
    ) m
    WHERE c.id = m.id AND c.excluded_employer IS DISTINCT FROM m.name
  `;
}

const ATS_DOMAIN = /(^|\.)(greenhouse\.io|lever\.co|myworkdayjobs\.com|workday\.com|ashbyhq\.com|smartrecruiters\.com|icims\.com|jobvite\.com|bamboohr\.com|workable\.com|recruitee\.com|rippling\.com)$/;
const MAJOR_BOARD = /(^|\.)(linkedin\.com|indeed\.com|builtin[a-z]*\.com|themuse\.com|dice\.com|simplify\.jobs|glassdoor\.com|wellfound\.com)$/;

function wordSet(text) {
  return new Set(String(text ?? "").toLowerCase().match(/[a-z][a-z0-9+#.-]{2,}/g) ?? []);
}

function jaccard(a, b) {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const w of a) if (b.has(w)) inter++;
  return inter / (a.size + b.size - inter);
}

function jobIds(url) {
  return String(url ?? "").match(/\d{7,}/g) ?? [];
}

// Last descriptive path segment, e.g. "director-of-developer-advocate-application-security".
function urlSlug(url) {
  try {
    const seg = new URL(url).pathname.split("/").filter(Boolean).pop() ?? "";
    return /^[a-z0-9-]+$/i.test(seg) && /[a-z]/i.test(seg) && seg.includes("-") ? seg.toLowerCase() : "";
  } catch {
    return "";
  }
}

function normEmployer(e) {
  return String(e ?? "").toLowerCase().replace(/[,.]|\b(inc|llc|ltd|corp|corporation|co|company|plc|group)\b/g, "").replace(/\s+/g, " ").trim();
}

function primaryRank(c) {
  const emp = normEmployer(c.employer).replace(/\s+/g, "");
  if (ATS_DOMAIN.test(c.domain)) return 0;
  if (emp && c.domain.replace(/[^a-z0-9]/g, "").includes(emp)) return 0;
  if (MAJOR_BOARD.test(c.domain)) return 1;
  return 2;
}

// Groups reposts of one job into a role: same employer and title, a shared job-board ID in
// the URL, or the same title with closely matching skills/page text when the employer is unknown.
async function clusterRoles() {
  const rows = await sql`
    SELECT id, canonical_url, domain, lower(coalesce(found_detail(job_title), title, '')) AS jt, employer,
           skills, highlights, left(page_text, 3000) AS page_text, first_seen_at, role_id, is_role_primary, found_via
    FROM candidates
  `;
  // bigint columns may arrive as strings; key everything by number. A missing employer or skills
  // (blank, unknown or a marker) is null here, so an unknown employer stays a wildcard.
  for (const r of rows) {
    r.id = Number(r.id);
    r.role_id = r.role_id == null ? null : Number(r.role_id);
    r.found_via = r.found_via == null ? null : Number(r.found_via);
    r.employer = found(r.employer);
    r.skills = found(r.skills);
  }
  const parent = new Map(rows.map((r) => [r.id, r.id]));
  const find = (x) => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(Math.max(ra, rb), Math.min(ra, rb));
  };

  // A page reached by following a link was already checked to be the same job as its source.
  const byId = new Map(rows.map((r) => [r.id, r]));
  for (const r of rows) {
    const src = r.found_via != null ? byId.get(r.found_via) : null;
    if (src && r.jt && r.jt === src.jt) union(r.id, src.id);
  }

  const byKey = new Map();
  const link = (key, id) => {
    if (byKey.has(key)) union(byKey.get(key), id);
    else byKey.set(key, id);
  };
  for (const r of rows) {
    const emp = normEmployer(r.employer);
    if (emp && r.jt) link(`e:${emp}|${r.jt}`, r.id);
    for (const jid of jobIds(r.canonical_url)) link(`j:${jid}`, r.id);
    // Shorter requisition IDs are only unique within one employer.
    if (emp) for (const jid of String(r.canonical_url).match(/(?<![\d])\d{4,6}(?![\d])/g) ?? []) link(`jr:${emp}|${jid}`, r.id);
    const slug = urlSlug(r.canonical_url);
    // Generic title slugs recur across employers, so a slug only links postings with the same (or no) employer.
    if (slug.length >= 25) link(`s:${emp || "?"}|${slug}`, r.id);
  }

  const byTitle = new Map();
  for (const r of rows) {
    if (!r.jt) continue;
    if (!byTitle.has(r.jt)) byTitle.set(r.jt, []);
    byTitle.get(r.jt).push({ ...r, words: wordSet(`${r.skills ?? ""} ${r.highlights ?? ""} ${r.page_text ?? ""}`) });
  }
  for (const group of byTitle.values()) {
    for (let i = 0; i < group.length; i++) {
      for (let j = i + 1; j < group.length; j++) {
        const a = group[i], b = group[j];
        const ea = normEmployer(a.employer), eb = normEmployer(b.employer);
        if (ea && eb && ea !== eb) continue;
        if (jaccard(a.words, b.words) >= 0.3) union(a.id, b.id);
      }
    }
  }

  const members = new Map();
  for (const r of rows) {
    const root = find(r.id);
    if (!members.has(root)) members.set(root, []);
    members.get(root).push(r);
  }
  let changed = 0;
  for (const [root, group] of members) {
    group.sort((a, b) => primaryRank(a) - primaryRank(b) || (b.employer ? 1 : 0) - (a.employer ? 1 : 0) || (b.skills ? 1 : 0) - (a.skills ? 1 : 0) || a.first_seen_at - b.first_seen_at || a.id - b.id);
    const primaryId = group[0].id;
    for (const r of group) {
      const isPrimary = r.id === primaryId;
      if (r.role_id === root && r.is_role_primary === isPrimary) continue;
      await sql`UPDATE candidates SET role_id = ${root}, is_role_primary = ${isPrimary} WHERE id = ${r.id}`;
      changed++;
    }
  }
  return { candidates: rows.length, roles: members.size, changed };
}

const ENRICH_BATCH = Number(process.env.ENRICH_BATCH ?? 5);
const SKIP_LINK_HOSTS = /(^|\.)(linkedin\.com|facebook\.com|twitter\.com|x\.com|instagram\.com|youtube\.com|google\.com|apple\.com|t\.co|bit\.ly)$/;
const EXA_CONTENTS_TIMEOUT_MS = 60000;
// Page text of this many characters or fewer is too little to extract details from.
const MIN_PAGE_TEXT_CHARS = 200;

// Why a posting's details could not be filled, typed so the caller knows whether trying again can
// help. `temporary` failures may clear up (an outage, a rate limit, an account problem); persistent
// ones will not (the page is gone, blocked or empty, or the model refuses it). `reason` is short,
// plain, secret-free text that goes into the "can't find: <reason>" marker.
class DetailError extends Error {
  constructor(reason, temporary) {
    super(reason);
    this.name = "DetailError";
    this.reason = reason;
    this.temporary = temporary;
  }
}

const REASON_MAX_CHARS = 120;

// Provider error text can echo keys, tokens and links: strip those, squash whitespace, cap the length.
function cleanReason(v, ...secrets) {
  let s = String(v ?? "");
  for (const k of [EXA_API_KEY, SERPER_API_KEY, ...secrets]) {
    if (k && String(k).length >= 6) s = s.split(String(k)).join("***");
  }
  s = s
    .replace(/https?:\/\/\S+/gi, "")
    .replace(/\bbearer\s+\S+/gi, "bearer ***")
    .replace(/\b(?:sk|or|exa|key|token)[-_](?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{8,}/gi, "***")
    .replace(/\b[A-Za-z0-9_-]{32,}\b/g, "***")
    .replace(/\s+/g, " ")
    .trim();
  return s.length > REASON_MAX_CHARS ? `${s.slice(0, REASON_MAX_CHARS - 3).trimEnd()}...` : s;
}

const withDetail = (prefix, detail) => {
  const d = String(detail ?? "").trim();
  return d ? `${prefix}: ${d}` : prefix;
};

const isTimeout = (err) => err?.name === "TimeoutError" || err?.name === "AbortError";

// The provider's own message from an error body: {"error": "..."} (Exa) or {"error": {"message": "..."}} (OpenRouter).
function providerMessage(text) {
  try {
    const j = JSON.parse(text);
    const e = j?.error;
    return String(typeof e === "string" ? e : (e?.message ?? j?.message ?? ""));
  } catch {
    return String(text ?? "");
  }
}

// Exa itself answered with an error status: nothing to do with the page. Every one is temporary so
// a request bug (400/404) or an account problem keeps surfacing in the logs and gets retried.
function exaHttpFailure(status, detail) {
  if (status === 429) return new DetailError("page fetch rate-limited", true);
  if (status === 401 || status === 402 || status === 403) return new DetailError(cleanReason(withDetail("page fetch account problem", detail)), true);
  if (status >= 500) return new DetailError("page fetch service error", true);
  return new DetailError("page fetch request rejected", true);
}

// /contents reports a per-URL failure inside a 200 response (statuses[].error.tag). `CRAWL_HTTP_<code>`
// carries the target page's own HTTP status; any tag not listed here, like a missing result, is
// treated as temporary.
function exaStatusFailure(error) {
  const tag = String(error?.tag ?? "");
  const http = Number(tag.match(/^CRAWL_HTTP_(\d{3})$/)?.[1]);
  if (tag === "CRAWL_NOT_FOUND") return new DetailError("page not found", false);
  if (tag === "SOURCE_NOT_AVAILABLE") return new DetailError("page not available (blocked or login required)", false);
  if (tag === "UNSUPPORTED_URL") return new DetailError("unsupported link", false);
  if (tag === "CRAWL_TIMEOUT" || tag === "CRAWL_LIVECRAWL_TIMEOUT") return new DetailError("page timed out", true);
  if (http === 404 || http === 410) return new DetailError("page gone", false);
  if (http === 401 || http === 403 || http === 451) return new DetailError("page blocks access", false);
  if (http === 408 || http === 429 || http >= 500) return new DetailError("page temporarily unavailable", true);
  return new DetailError("page fetch failed", true);
}

// One outcome per requested URL, in order: { page } when Exa read it, or { failure } (a DetailError)
// when it did not. A request-level failure (HTTP error, network, timeout) throws a DetailError instead.
async function exaContents(urls, withLinks, fresh = false) {
  let r;
  try {
    r = await fetch("https://api.exa.ai/contents", {
      method: "POST",
      headers: { "x-api-key": EXA_API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify({
        urls,
        ...EXA_CONTENTS,
        ...(withLinks ? { extras: { links: 50 } } : {}),
        ...(fresh ? { maxAgeHours: 0 } : {}),
      }),
      signal: AbortSignal.timeout(EXA_CONTENTS_TIMEOUT_MS),
    });
  } catch (err) {
    throw new DetailError(isTimeout(err) ? "page fetch timed out" : "page fetch network error", true);
  }
  if (!r.ok) throw exaHttpFailure(r.status, providerMessage(await r.text().catch(() => "")));
  let body;
  try {
    body = await r.json();
  } catch {
    throw new DetailError("page fetch failed", true);
  }
  const results = Array.isArray(body?.results) ? body.results : [];
  const statuses = Array.isArray(body?.statuses) ? body.statuses : [];
  // Results and statuses name the requested URL (url / id); with one URL there is nothing to match.
  const pick = (list, u) => list.find((x) => x?.url === u || x?.id === u) ?? (urls.length === 1 ? list[0] : undefined);
  return urls.map((u) => {
    const page = pick(results, u);
    if (page) return { page };
    const status = pick(statuses, u);
    return { failure: status?.status === "error" ? exaStatusFailure(status.error) : new DetailError("page fetch failed", true) };
  });
}

// One URL's page, or a DetailError saying why Exa could not read it.
async function exaPage(url, withLinks, fresh = false) {
  const [res] = await exaContents([url], withLinks, fresh);
  if (res.failure) throw res.failure;
  return res.page;
}

// Picks the link most likely to be the employer's own or ATS posting of this job.
function pickPrimaryLink(links, fromDomain, employer) {
  const emp = normEmployer(employer).replace(/[^a-z0-9]/g, "");
  let best = null;
  let bestScore = 0;
  for (const link of links ?? []) {
    const canonical = canonicalizeUrl(link);
    if (!canonical) continue;
    const host = new URL(canonical).hostname;
    if (host === fromDomain || SKIP_LINK_HOSTS.test(host)) continue;
    let score = 0;
    if (ATS_DOMAIN.test(host)) score += 3;
    if (emp.length >= 4 && host.replace(/[^a-z0-9]/g, "").includes(emp)) score += 2;
    if (/job|career|position|opening|apply/i.test(canonical)) score += 1;
    if (/\d{5,}/.test(canonical)) score += 1;
    if (score >= 3 && score > bestScore) {
      best = link;
      bestScore = score;
    }
  }
  return best;
}

// Follows a page's link to the employer's own or ATS posting of the same job and adds that posting
// as a candidate (found_via this one) when it carries the same job title; it is new this cycle, so
// fillDetails() picks it up. Best effort: a failure here goes into `errors` and never counts against
// the page's own details.
async function followPrimaryLink(row, page, c, tally, errors) {
  try {
    if (ATS_DOMAIN.test(row.domain)) return;
    const link = pickPrimaryLink(page.extras?.links, row.domain, c.employer ?? found(row.employer));
    if (!link) return;
    const canonical = canonicalizeUrl(link);
    await sql`UPDATE candidates SET primary_source_url = ${canonical} WHERE id = ${row.id}`;
    tally.followed++;
    const [existing] = await sql`SELECT id FROM candidates WHERE canonical_url = ${canonical}`;
    if (existing) return;
    const target = await exaPage(canonical, false);
    // Only keep the linked page if it carries the same job title; an employer match alone let
    // careers index pages and unrelated jobs through.
    const targetContents = contentsFrom(target);
    const sameTitle = normalizeTitle(target.title)?.toLowerCase() === String(found(row.job_title) ?? "").toLowerCase();
    if (!sameTitle) return;
    const [inserted] = await sql`
      INSERT INTO candidates (canonical_url, url, domain, title, sources, found_via)
      VALUES (${canonical}, ${link}, ${new URL(canonical).hostname}, ${target.title ?? null},
              ARRAY['link']::text[], ${row.id})
      ON CONFLICT (canonical_url) DO NOTHING
      RETURNING id
    `;
    if (inserted) {
      await saveContents(inserted.id, targetContents);
      tally.added++;
    }
  } catch (err) {
    errors.push(`link ${row.canonical_url}: ${err.reason ?? err.message}`.slice(0, 300));
  }
}

// Link stage: fetch the page of each role's primary posting once, to find a link to the employer's
// own or ATS posting of the job, which role clustering then prefers. Filling a posting's details is
// fillDetails()'s job; it follows links too whenever it has to fetch a page itself.
async function enrichCandidates() {
  const rows = await sql`
    SELECT id, canonical_url, domain, employer, job_title
    FROM candidates
    WHERE excluded_employer IS NULL
      -- No follow-up spend on roles already ruled out for a known reason.
      AND NOT EXISTS (
        SELECT 1 FROM roles r
        WHERE r.role_id = candidates.role_id
          AND (r.excluded_employer IS NOT NULL
               OR r.remote_eligibility IN ('hybrid', 'onsite')
               OR r.posted_at < now() - interval '1 month')
      )
      AND enrich_attempts < 1 AND is_role_primary AND primary_source_url IS NULL
    ORDER BY id
    LIMIT ${ENRICH_BATCH}
  `;
  let filled = 0;
  const tally = { followed: 0, added: 0 };
  const errors = [];
  for (const row of rows) {
    await sql`UPDATE candidates SET enrich_attempts = enrich_attempts + 1, enriched_at = now() WHERE id = ${row.id}`;
    try {
      const page = await exaPage(row.canonical_url, true);
      const c = contentsFrom(page);
      await saveContents(row.id, c);
      filled++;
      await followPrimaryLink(row, page, c, tally, errors);
    } catch (err) {
      errors.push(`${row.canonical_url}: ${err.reason ?? err.message}`.slice(0, 300));
    }
  }
  if (rows.length) {
    console.log(JSON.stringify({ at: new Date().toISOString(), event: "enriched", checked: rows.length, filled, ...tally, errors }));
  }
  return rows.length;
}

const LLM_BATCH = Number(process.env.LLM_BATCH ?? 8);
const LLM_MODELS = (process.env.LLM_MODELS ?? "minimax/minimax-m2.7,openai/gpt-oss-120b").split(",");
const OPENROUTER_KEY_FILE = process.env.OPENROUTER_KEY_FILE ?? "/secrets/openrouter/api_key";
const EXTRACT_VERSION = 3;

async function openrouterKey() {
  try {
    const v = (await Bun.file(OPENROUTER_KEY_FILE).text()).trim();
    return v || null;
  } catch {
    return null;
  }
}

// Models sometimes answer "Not specified" instead of leaving a field empty.
function meaningful(v) {
  const t = found(v);
  return !t || /^(n\/?a|none|unknown|not (specified|stated|mentioned|provided)\b.*)$/i.test(t) ? null : t;
}

function parseJsonLoose(text) {
  const t = String(text ?? "").replace(/^```(?:json)?\s*|\s*```$/g, "").trim();
  try {
    return JSON.parse(t);
  } catch {
    const m = t.match(/\{[\s\S]*\}/);
    return m ? JSON.parse(m[0]) : null;
  }
}

const BULLET_GUIDE = `For skills and success, give 3 to 8 terse keyword phrases each (2 to 5 words), not sentences.
Keep named frameworks, standards, tools, domains and concrete duties; abbreviate standards (ISO 27001 -> ISO 27k).
Drop years of experience, soft skills, and filler such as strong, deep, proven, experience in, understanding of, ability to, the role involves, success will be measured by.

Example success. Text: "Success will be measured by the ability to define and govern security architecture that aligns with business strategy and reduces cyber risk. The role involves leading cross-functional teams to implement security patterns across cloud, data, AI, and identity platforms."
Answer: ["define and govern security architecture", "lead teams", "cloud, data, AI, identity"]

Example skills. Text: "10+ years of experience in cybersecurity, security architecture, or information security with focus on enterprise architecture and solution design; deep expertise in security architecture frameworks, secure design principles, and enterprise technology environments; strong understanding of cybersecurity frameworks (NIST CSF, ISO 27001) and regulatory requirements; experience leading architecture reviews, defining standards, and guiding secure solution development."
Answer: ["security architecture frameworks", "secure design principles", "NIST CSF", "ISO 27k", "architecture reviews", "defining standards", "solution development"]`;

// Turns a model's list (or a stray string) into one bullet per line for Excel.
function bullets(v) {
  const items = (Array.isArray(v) ? v : String(v ?? "").split(/\n|;\s*/))
    .map((x) => String(x).replace(/^[\s•*-]+/, "").trim())
    .filter((x) => meaningful(x));
  return items.length ? items.map((x) => `• ${x}`).join("\n") : null;
}

// OpenRouter's error codes, from an HTTP status or from an error inside a 200 response. 404 means the
// model is not there: llmExtract() tries the next one.
function modelStatusFailure(status, detail, key) {
  if (status === 400) return new DetailError("model could not read the page", false);
  if (status === 403) return new DetailError("model declined the page", false);
  if (status === 404) return new DetailError("model unavailable", false);
  if (status === 408 || status === 504) return new DetailError("model timed out", true);
  if (status === 429) return new DetailError("model rate-limited", true);
  if (status === 401 || status === 402) return new DetailError(cleanReason(withDetail("model account problem", detail), key), true);
  if (status >= 500) return new DetailError("model service error", true);
  return new DetailError(Number.isFinite(status) ? `model request failed (HTTP ${status})` : "model request failed", true);
}

// One model's answer as a JSON object, or a DetailError saying why there is none.
async function llmCall(key, model, prompt) {
  let r;
  try {
    r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: prompt }],
        response_format: { type: "json_object" },
        temperature: 0,
      }),
      signal: AbortSignal.timeout(90000),
    });
  } catch (err) {
    throw new DetailError(isTimeout(err) ? "model timed out" : "model network error", true);
  }
  if (!r.ok) throw modelStatusFailure(r.status, providerMessage(await r.text().catch(() => "")), key);
  let body;
  try {
    body = await r.json();
  } catch {
    throw new DetailError("model reply unreadable", true);
  }
  const choice = body?.choices?.[0];
  // OpenRouter also reports errors inside a 200: a top-level error with no choices, or finish_reason "error".
  if (body?.error && !choice) throw modelStatusFailure(Number(body.error.code), String(body.error.message ?? ""), key);
  if (choice?.finish_reason === "error") throw new DetailError("model failed mid-reply", true);
  const content = choice?.message?.content;
  if (choice?.finish_reason === "length" && (typeof content !== "string" || !content.trim())) {
    throw new DetailError("model ran out of output room", false);
  }
  let out = null;
  try {
    out = parseJsonLoose(content);
  } catch {
    // unreadable, handled below
  }
  if (!out || typeof out !== "object" || Array.isArray(out)) throw new DetailError("model reply unreadable", true);
  return out;
}

// Tries the models in order. When every one fails the failure is temporary if any model's was (the
// last such reason is the one reported), else persistent with the last reason.
async function llmExtract(key, { title, url, text, stated }) {
  const today = new Date().toISOString().slice(0, 10);
  const prompt = [
    `Today is ${today}. Extract facts about this job posting. Answer with one JSON object only, matching this JSON schema:`,
    JSON.stringify(ROLE_SCHEMA),
    `Also include "is_job_posting": true if the page is a single job posting, false if it is a list of jobs, a careers or benefits page, or anything else.`,
    `Also include "job_title": the job title alone, with no company, location, site name, salary or words like "job" or "remote".`,
    `Use empty strings or empty lists where the page does not say.`,
    BULLET_GUIDE,
    ...(stated ? [`Stated qualifications and responsibilities from the listing:`, stated] : []),
    `Title: ${title ?? ""}`,
    `URL: ${url}`,
    `Page text:`,
    String(text).slice(0, 12000),
  ].join("\n\n");
  const failures = [];
  for (const model of LLM_MODELS) {
    try {
      return { model, out: await llmCall(key, model, prompt) };
    } catch (err) {
      failures.push(err instanceof DetailError ? err : new DetailError("model network error", true));
    }
  }
  const temporary = failures.filter((f) => f.temporary);
  throw (temporary.length ? temporary.at(-1) : failures.at(-1)) ?? new DetailError("no model configured", true);
}

// Our model's answer about a posting, as the values to store (null = the page does not state it).
async function extractWithLlm(key, row, text) {
  const statedSkills = found(row.stated_skills);
  const statedSuccess = found(row.stated_success);
  const stated = [statedSkills && `Qualifications: ${statedSkills}`, statedSuccess && `Responsibilities: ${statedSuccess}`]
    .filter(Boolean)
    .join("\n");
  const { model, out } = await llmExtract(key, { title: row.title, url: row.canonical_url, text, stated });
  return {
    model,
    c: {
      employer: cleanEmployer(out.employer),
      employerSector: ["security", "technology", "other"].includes(String(out.employer_sector)) ? String(out.employer_sector) : null,
      skills: bullets(out.skills),
      success: bullets(out.success),
      remote: ["remote", "hybrid", "onsite"].includes(String(out.remote_eligibility)) ? String(out.remote_eligibility) : null,
      postedAt: parsePostedDate(out.posted_date),
      isJob: typeof out.is_job_posting === "boolean" ? out.is_job_posting : null,
      jobTitle: meaningful(out.job_title),
    },
  };
}

// Postings first seen from this date on get their details filled even if the cycle that found them
// ended early; older ones only when they are a considered role's primary (the jobs found before
// detail markers existed keep their blanks).
const DETAILS_SINCE = "2026-10-03T00:00:00Z";

function newCounts() {
  return { targets: 0, filled: 0, not_mentioned: 0, cant_find: 0, pending: 0, max_retries: 0, followed: 0, added: 0, errors: [] };
}

// Runs fillDetails() batch after batch until a batch finds no target, and returns how many postings
// it attempted. A thrown error leaves the tally of what was done so far in `counts`.
async function drainDetails(cycleStart, retryFrom, counts) {
  const start = counts.targets;
  for (let i = 0; !shuttingDown; i++) {
    if (i >= MAX_LOOP_ITERATIONS) {
      loopCapHit(retryFrom === null ? "details" : "details_retry");
      break;
    }
    const before = counts.targets;
    await fillDetails(cycleStart, retryFrom, counts);
    if (counts.targets === before) break;
  }
  return counts.targets - start;
}

// One log line per pass (a pass drains every batch of targets): how each target ended.
function logDetails(pass, c) {
  const { errors, ...counts } = c;
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "details_filled", pass, ...counts, errors: errors.length ? errors.slice(0, 20) : undefined }));
}

// Extraction already succeeded for this posting, so what the page does not state is final: every
// detail still missing (blank, unknown or a can't-find marker) becomes "not mentioned".
async function settleDetails(id) {
  await sql`
    UPDATE candidates SET
      employer = coalesce(found_detail(employer), ${NOT_MENTIONED}),
      employer_sector = coalesce(found_detail(employer_sector), ${NOT_MENTIONED}),
      skills = coalesce(found_detail(skills), ${NOT_MENTIONED}),
      success = coalesce(found_detail(success), ${NOT_MENTIONED}),
      remote_eligibility = coalesce(found_detail(remote_eligibility), ${NOT_MENTIONED}),
      job_title = coalesce(found_detail(job_title), ${NOT_MENTIONED}),
      detail_pending = false,
      detail_error = NULL
    WHERE id = ${id}
  `;
  return "not_mentioned";
}

// Writes the model's values. Values that came from Google Jobs' structured data are kept; the model
// fills whatever is still missing, and what it left empty (or called unknown) becomes "not mentioned".
async function writeExtraction(row, c, model) {
  const g = Boolean(row.from_google);
  const [res] = await sql`
    UPDATE candidates SET
      employer = coalesce(found_detail(CASE WHEN ${g} THEN coalesce(found_detail(employer), ${c.employer}, employer) ELSE coalesce(${c.employer}, employer) END), ${NOT_MENTIONED}),
      employer_sector = coalesce(found_detail(coalesce(${c.employerSector}, employer_sector)), ${NOT_MENTIONED}),
      skills = coalesce(found_detail(coalesce(${c.skills}, skills)), ${NOT_MENTIONED}),
      success = coalesce(found_detail(coalesce(${c.success}, success)), ${NOT_MENTIONED}),
      remote_eligibility = coalesce(found_detail(CASE
            WHEN ${g} AND found_detail(remote_eligibility) IS NOT NULL THEN remote_eligibility
            ELSE coalesce(${c.remote}, remote_eligibility) END), ${NOT_MENTIONED}),
      posted_at = CASE WHEN ${g} THEN coalesce(posted_at, ${c.postedAt}) ELSE coalesce(${c.postedAt}, posted_at) END,
      is_job_posting = CASE WHEN ${c.isJob}::boolean IS NULL THEN is_job_posting ELSE (${c.isJob}::boolean AND coalesce(is_job_posting, true)) END,
      job_title = coalesce(found_detail(coalesce(${c.jobTitle}::text, job_title)), ${NOT_MENTIONED}),
      job_title_version = CASE WHEN ${c.jobTitle}::text IS NULL THEN job_title_version ELSE ${TITLE_VERSION}::int END,
      extract_version = ${EXTRACT_VERSION},
      extract_model = ${model},
      extracted_at = now(),
      detail_pending = false,
      detail_error = NULL
    WHERE id = ${row.id}
    RETURNING ${NOT_MENTIONED} IN (employer, employer_sector, skills, success, remote_eligibility, job_title) AS has_not_mentioned
  `;
  return res.has_not_mentioned ? "not_mentioned" : "filled";
}

// Gets the page text details are extracted from: the page's stored text when search results brought
// enough of it, else Exa's fetch of the page (which also follows its link to the primary source).
async function fetchPageText(row, counts) {
  await sql`UPDATE candidates SET enrich_attempts = enrich_attempts + 1, enriched_at = now() WHERE id = ${row.id}`;
  // A retry asks Exa for a fresh crawl, since its cached copy already came back incomplete.
  const page = await exaPage(row.canonical_url, true, row.enrich_attempts >= 1);
  const c = contentsFrom(page);
  await saveContents(row.id, c);
  await followPrimaryLink(row, page, c, counts, counts.errors);
  if ((c.pageText?.length ?? 0) <= MIN_PAGE_TEXT_CHARS) throw new DetailError("page has no readable text", false);
  return c.pageText;
}

// A failed attempt. Persistent: every missing detail gets "can't find: <reason>". Temporary: the
// posting waits for a retry, until MAX_DETAIL_TRIES temporary failures, when it gets
// "can't find: <reason>; max retries hit". A failure in the model step also stamps extraction as
// failed (extract_version set, extract_model null), which the roles view reports as "details could
// not be extracted"; a page failure leaves extraction unstamped.
async function recordFailure(row, err, step, counts) {
  counts.errors.push(`${row.canonical_url}: ${err.reason}${err.temporary ? " (temporary)" : ""}`.slice(0, 300));
  const tries = row.detail_attempts + (err.temporary ? 1 : 0);
  const gaveUp = err.temporary && tries >= MAX_DETAIL_TRIES;
  if (err.temporary && !gaveUp) {
    await sql`
      UPDATE candidates SET detail_attempts = ${tries}, detail_pending = true, detail_error = ${err.reason}
      WHERE id = ${row.id}
    `;
    return "pending";
  }
  const marker = `${CANT_FIND}: ${err.reason}${gaveUp ? `; ${MAX_RETRIES_NOTE}` : ""}`;
  const stamp = step === "model";
  await sql`
    UPDATE candidates SET
      employer = CASE WHEN found_detail(employer) IS NULL THEN ${marker} ELSE employer END,
      employer_sector = CASE WHEN found_detail(employer_sector) IS NULL THEN ${marker} ELSE employer_sector END,
      skills = CASE WHEN found_detail(skills) IS NULL THEN ${marker} ELSE skills END,
      success = CASE WHEN found_detail(success) IS NULL THEN ${marker} ELSE success END,
      remote_eligibility = CASE WHEN found_detail(remote_eligibility) IS NULL THEN ${marker} ELSE remote_eligibility END,
      job_title = CASE WHEN found_detail(job_title) IS NULL THEN ${marker} ELSE job_title END,
      extract_version = CASE WHEN ${stamp} THEN ${EXTRACT_VERSION}::int ELSE extract_version END,
      extract_model = CASE WHEN ${stamp} THEN NULL ELSE extract_model END,
      extracted_at = CASE WHEN ${stamp} THEN now() ELSE extracted_at END,
      detail_attempts = ${tries},
      detail_pending = false,
      detail_error = ${err.reason}
    WHERE id = ${row.id}
  `;
  return gaveUp ? "max_retries" : "cant_find";
}

// Fills one posting's details and says how it ended: filled, not_mentioned, cant_find, pending or
// max_retries. The attempt is stamped first, so even a crash leaves the posting out of this pass.
async function fillOne(row, key, counts) {
  await sql`UPDATE candidates SET detail_tried_at = now() WHERE id = ${row.id}`;
  let step = "page";
  try {
    // Page text and extraction that already succeeded are not repeated.
    if (row.extracted) return await settleDetails(row.id);
    const text = row.has_text ? row.page_text : await fetchPageText(row, counts);
    step = "model";
    const result = await extractWithLlm(key, row, text);
    step = "write";
    return await writeExtraction(row, result.c, result.model);
  } catch (err) {
    // Anything unexpected (a bug, a database hiccup) is retried like a temporary failure, so one bad
    // posting can neither stop the pass nor be skipped silently.
    const e = err instanceof DetailError ? err : new DetailError(cleanReason(`internal error: ${err?.message ?? err}`), true);
    return await recordFailure(row, e, step, counts);
  }
}

// Fills the details (employer, sector, skills, success, remote, job title) of up to LLM_BATCH
// postings still in play, tallying how each ended into `counts` as it goes (a pass shares one
// tally across its batches and keeps it if a batch throws). Targets are postings that are new this
// cycle (first seen at or after `cycleStart`, or first seen since DETAILS_SINCE with no outcome
// recorded, so a cycle cut short leaves nothing behind), primaries of roles currently considered, and
// postings waiting on a retry, each only while some detail is missing and the role is not ruled
// out. A posting is attempted at most once per pass: the normal pass skips anything tried since
// `cycleStart`; the retry pass (`retryFrom` = when it started) takes only postings that went
// pending earlier in this cycle and not yet in this pass.
async function fillDetails(cycleStart, retryFrom = null, counts = newCounts()) {
  const key = await openrouterKey();
  if (!key) {
    console.error(JSON.stringify({ at: new Date().toISOString(), event: "details_skipped", reason: "no openrouter key" }));
    return counts;
  }
  const retry = retryFrom !== null;
  const rows = await sql`
    WITH rr AS (
      SELECT role_id, considered,
             coalesce(excluded_employer IS NOT NULL OR remote_eligibility IN ('hybrid', 'onsite')
                      OR posted_at < now() - interval '1 month' OR live_status = 'closed', false) AS ruled_out
      FROM roles
    )
    SELECT c.id, c.title, c.canonical_url, c.domain, c.employer, c.job_title, c.page_text,
           c.skills AS stated_skills, c.success AS stated_success, c.enrich_attempts, c.detail_attempts,
           coalesce('google_jobs' = ANY(c.sources), false) AS from_google,
           x.has_text, x.extracted
    FROM candidates c
    LEFT JOIN rr ON rr.role_id = c.role_id
    CROSS JOIN LATERAL (
      SELECT coalesce(c.extract_version = ${EXTRACT_VERSION} AND c.extract_model IS NOT NULL, false) AS extracted,
             coalesce(length(c.page_text) > ${MIN_PAGE_TEXT_CHARS}, false) AS has_text,
             -- missing is blank, unknown or a marker. unsettled is missing and not already not mentioned.
             (found_detail(c.employer) IS NULL OR found_detail(c.employer_sector) IS NULL
              OR found_detail(c.skills) IS NULL OR found_detail(c.success) IS NULL
              OR found_detail(c.remote_eligibility) IS NULL OR found_detail(c.job_title) IS NULL) AS missing,
             ((found_detail(c.employer) IS NULL AND c.employer IS DISTINCT FROM ${NOT_MENTIONED})
              OR (found_detail(c.employer_sector) IS NULL AND c.employer_sector IS DISTINCT FROM ${NOT_MENTIONED})
              OR (found_detail(c.skills) IS NULL AND c.skills IS DISTINCT FROM ${NOT_MENTIONED})
              OR (found_detail(c.success) IS NULL AND c.success IS DISTINCT FROM ${NOT_MENTIONED})
              OR (found_detail(c.remote_eligibility) IS NULL AND c.remote_eligibility IS DISTINCT FROM ${NOT_MENTIONED})
              OR (found_detail(c.job_title) IS NULL AND c.job_title IS DISTINCT FROM ${NOT_MENTIONED})) AS unsettled
    ) x
    WHERE c.excluded_employer IS NULL
      -- No follow-up spend on roles already ruled out for a known reason.
      AND NOT coalesce(rr.ruled_out, false)
      -- An extracted posting only gets not mentioned written over what is still blank.
      AND x.missing AND (x.unsettled OR NOT x.extracted)
      AND CASE WHEN ${retry}::boolean
               THEN c.detail_pending AND c.detail_tried_at >= ${cycleStart}::timestamptz AND c.detail_tried_at < ${retryFrom ?? cycleStart}::timestamptz
               ELSE (c.detail_tried_at IS NULL OR c.detail_tried_at < ${cycleStart}::timestamptz)
                    AND (c.first_seen_at >= ${cycleStart}::timestamptz OR c.detail_pending
                         -- Nothing recorded yet (never tried, or its attempt died before an outcome).
                         OR (c.first_seen_at >= ${DETAILS_SINCE}::timestamptz AND c.detail_error IS NULL)
                         -- A considered primary whose page was already tried and has no usable text is not
                         -- fetched again every cycle. It is picked up again once search results bring text.
                         OR (c.is_role_primary AND coalesce(rr.considered, false)
                             AND (x.has_text OR c.detail_tried_at IS NULL)))
          END
    ORDER BY c.is_role_primary DESC NULLS LAST, c.id
    LIMIT ${LLM_BATCH}
  `;
  for (const row of rows) {
    if (shuttingDown) break;
    const outcome = await fillOne(row, key, counts);
    counts.targets++;
    counts[outcome]++;
  }
  return counts;
}

const LIVE_BATCH = Number(process.env.LIVE_BATCH ?? 10);
const CLOSED_TEXT =
  /no longer (accepting applications|available|open|active)|position (has been|is) (filled|closed)|job (has|is) (expired|closed|no longer)|this job (posting )?(has been|is) (removed|closed|filled)|posting (has )?(expired|closed)|job not found|requisition (is )?closed|applications? (are |is )?closed/i;

async function checkLive(url) {
  try {
    const r = await fetch(url, {
      redirect: "follow",
      signal: AbortSignal.timeout(15000),
      headers: { "User-Agent": "Mozilla/5.0 (compatible; simplesalt-crawl/1.0)", Accept: "text/html,*/*" },
    });
    if (r.status === 404 || r.status === 410) return { status: "closed", detail: `HTTP ${r.status}` };
    if (!r.ok) return { status: "unknown", detail: `HTTP ${r.status}` };
    const text = (await r.text()).slice(0, 400000).replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ");
    const m = text.match(CLOSED_TEXT);
    if (m) return { status: "closed", detail: m[0] };
    return { status: "live", detail: `HTTP ${r.status}` };
  } catch (err) {
    return { status: "unknown", detail: String(err.message).slice(0, 120) };
  }
}

// Checks the primary posting of every role still in consideration, oldest check first, skipping
// any checked in the last day; one batch per call, returning how many it checked.
async function checkLiveness() {
  const rows = await sql`
    SELECT c.id, c.canonical_url FROM candidates c
    JOIN roles r ON r.role_id = c.role_id
    WHERE c.is_role_primary AND r.considered
      AND (c.live_checked_at IS NULL OR c.live_checked_at < now() - interval '1 day')
    ORDER BY c.live_checked_at NULLS FIRST, c.id
    LIMIT ${LIVE_BATCH}
  `;
  const tally = { live: 0, closed: 0, unknown: 0 };
  for (const row of rows) {
    const res = await checkLive(row.canonical_url);
    tally[res.status]++;
    await sql`
      UPDATE candidates SET live_status = ${res.status}, live_detail = ${res.detail}, live_checked_at = now()
      WHERE id = ${row.id}
    `;
  }
  if (rows.length) console.log(JSON.stringify({ at: new Date().toISOString(), event: "liveness_checked", ...tally }));
  return rows.length;
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
    const { text, ...rawWithoutText } = o;
    return {
      url: o.url,
      title: o.title ?? null,
      snippet: null,
      publishedAt: parseDate(o.publishedDate),
      rank: i + 1,
      raw: rawWithoutText,
      contents: contentsFrom(o),
    };
  });
}

// Job boards and scraper sites Exa sometimes names as the employer.
const AGGREGATOR_NAMES = /^(jobgether|jobsy|jobtrail|taskium|hiring ?camp|asian ?careers|jobs ?radar|jobera|notify ?careers|built ?in.*|the ?muse|dice|simplify( jobs)?|swooped|haystack|ihire.*|jobscroller|worksynergy|workvista|remoteforge|skillcore|jobgrow|linkedin|indeed|glassdoor|ziprecruiter|ms)$/i;

function cleanEmployer(e) {
  const v = typeof e === "string" ? found(e) : null;
  return v && !AGGREGATOR_NAMES.test(normEmployer(v)) ? v : null;
}

function contentsFrom(o) {
  const summary = parseSummary(o.summary);
  return {
    employer: cleanEmployer(summary.employer),
    employerSector: ["security", "technology", "other"].includes(String(summary.employer_sector)) ? String(summary.employer_sector) : null,
    skills: summary.skills || null,
    success: summary.success || null,
    remoteEligibility: normalizeRemote(summary.remote_eligibility),
    highlights: Array.isArray(o.highlights) && o.highlights.length ? o.highlights.join(" … ") : null,
    pageText: typeof o.text === "string" ? o.text.slice(0, PAGE_TEXT_MAX_CHARS) : null,
    postedAt: parsePostedDate(summary.posted_date),
  };
}

function parsePostedDate(v) {
  const m = String(v ?? "").match(/^\d{4}-\d{2}-\d{2}$/);
  if (!m) return null;
  const d = new Date(`${m[0]}T00:00:00Z`);
  return Number.isNaN(d.getTime()) || d > new Date() ? null : d;
}

// Every cycle reruns every search, so this also runs when a posting is seen again. Once our model has
// extracted a posting, the details it found are kept; a repeat sighting only fills what is still
// missing. The posted date only ever moves earlier: relative ages ("30+ days ago") would otherwise
// creep forward with each sighting.
async function saveContents(candidateId, c) {
  await sql`
    UPDATE candidates SET
      employer = CASE WHEN extract_model IS NOT NULL AND found_detail(employer) IS NOT NULL
                      THEN employer ELSE COALESCE(${c.employer}, employer) END,
      employer_sector = CASE WHEN extract_model IS NOT NULL AND found_detail(employer_sector) IS NOT NULL
                             THEN employer_sector ELSE COALESCE(${c.employerSector ?? null}, employer_sector) END,
      skills = CASE WHEN extract_model IS NOT NULL AND found_detail(skills) IS NOT NULL
                    THEN skills ELSE COALESCE(${c.skills}, skills) END,
      success = CASE WHEN extract_model IS NOT NULL AND found_detail(success) IS NOT NULL
                     THEN success ELSE COALESCE(${c.success}, success) END,
      remote_eligibility = CASE WHEN extract_model IS NOT NULL AND found_detail(remote_eligibility) IS NOT NULL
                                THEN remote_eligibility
                                WHEN ${c.remoteEligibility} = 'unknown' AND remote_eligibility IS NOT NULL
                                THEN remote_eligibility ELSE ${c.remoteEligibility} END,
      highlights = COALESCE(${c.highlights}, highlights),
      page_text = COALESCE(${c.pageText}, page_text),
      posted_at = LEAST(${c.postedAt ?? null}::timestamptz, posted_at),
      contents_fetched_at = now()
    WHERE id = ${candidateId}
  `;
}

function relativeAge(v) {
  const m = String(v ?? "").match(/(\d+)\+?\s*(hour|day|week|month|year)s?\s+ago/i);
  if (!m) return /just|today|hour/i.test(String(v ?? "")) ? new Date() : null;
  const days = { hour: 1 / 24, day: 1, week: 7, month: 30, year: 365 }[m[2].toLowerCase()] * Number(m[1]);
  return new Date(Date.now() - days * 86400000);
}

// SerpApi puts Google's chips ("9 days ago", "Work from home", "Full-time") in extensions[];
// detected_extensions is only sometimes present.
function googleExtensions(job) {
  return [...(job.extensions ?? []), job.detected_extensions?.posted_at].filter((x) => typeof x === "string");
}

function googleWorkFromHome(job) {
  return Boolean(job.detected_extensions?.work_from_home) || googleExtensions(job).some((x) => /work from home|remote/i.test(x));
}

function googlePostedAt(job) {
  for (const x of googleExtensions(job)) {
    const d = relativeAge(x);
    if (d && /ago|today|just/i.test(x)) return d;
  }
  return null;
}

function remoteFromGoogle(job) {
  if (googleWorkFromHome(job)) return "remote";
  const text = `${job.location ?? ""} ${job.description ?? ""}`;
  if (/\bhybrid\b/i.test(text)) return "hybrid";
  if (/\b(fully remote|100% remote|remote[- ]first|work from home|remote,? (us|usa|united states))\b/i.test(text)) return "remote";
  if (/\b(on-?site|in[- ]office)\b/i.test(text)) return "onsite";
  return "unknown";
}

function highlightItems(job, title) {
  const h = (job.job_highlights ?? []).find((x) => new RegExp(title, "i").test(x.title ?? ""));
  return h?.items?.length ? h.items.join("; ") : null;
}

// Best apply link: ATS or employer page first, then major boards, then anything else.
function bestApplyLink(job) {
  const links = (job.apply_options ?? []).map((o) => o.link).filter(Boolean);
  const ranked = links
    .map((link) => {
      const c = canonicalizeUrl(link);
      if (!c) return null;
      return { link, rank: primaryRank({ domain: new URL(c).hostname, employer: job.company_name }) };
    })
    .filter(Boolean)
    .sort((a, b) => a.rank - b.rank);
  return ranked[0]?.link ?? job.share_link ?? null;
}

async function googleJobsSearch(q, key) {
  const params = new URLSearchParams({ engine: "google_jobs", q, gl: "us", hl: "en", api_key: key });
  const r = await fetch(`https://serpapi.com/search.json?${params}`);
  if (!r.ok) throw new Error(`serpapi ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const body = await r.json();
  if (body.error && !/hasn't returned any results/i.test(body.error)) throw new Error(`serpapi: ${body.error}`);
  return (body.jobs_results ?? []).map((job, i) => {
    const { description, ...rawWithoutDescription } = job;
    return {
      url: bestApplyLink(job),
      title: job.title ?? null,
      snippet: null,
      publishedAt: null,
      rank: i + 1,
      raw: rawWithoutDescription,
      contents: {
        employer: cleanEmployer(job.company_name),
        employerSector: null,
        skills: highlightItems(job, "qualif"),
        success: highlightItems(job, "responsib"),
        remoteEligibility: remoteFromGoogle(job),
        highlights: null,
        pageText: typeof description === "string" ? description.slice(0, PAGE_TEXT_MAX_CHARS) : null,
        postedAt: googlePostedAt(job),
      },
    };
  });
}

async function runGoogleJobs(search, run, key, counts, errors) {
  for (const q of search.google_jobs_queries ?? []) {
    try {
      const results = (await googleJobsSearch(q, key)).filter((r) => r.url);
      counts.google_jobs += results.length;
      const r = await storeResults({ search, run, source: "google_jobs", query: q, results });
      counts.inserted += r.inserted;
      counts.updated += r.updated;
    } catch (err) {
      errors.push(`google_jobs[${q}]: ${err.message}`);
    }
  }
  await sql`UPDATE searches SET google_jobs_run_at = now() WHERE id = ${search.id}`;
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

    if (r.contents) await saveContents(candidate.id, r.contents);

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
        to_jsonb(exa_queries) AS exa_queries,
        to_jsonb(google_jobs_queries) AS google_jobs_queries
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
  const counts = { serper: 0, exa: 0, google_jobs: 0, inserted: 0, updated: 0 };
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

  const googleKey = await serpapiKey();
  if (googleKey && (search.google_jobs_queries ?? []).length) await runGoogleJobs(search, run, googleKey, counts, errors);

  const status = errors.length ? "error" : "ok";
  const errorText = errors.length ? errors.join(" | ") : null;

  await sql`
    UPDATE search_runs SET
      finished_at = now(),
      google_jobs_results = ${counts.google_jobs},
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
      google_jobs_results: counts.google_jobs,
      inserted: counts.inserted,
      updated: counts.updated,
      errors: errors.length,
      error_detail: errors.length ? errors.map((e) => e.slice(0, 300)) : undefined,
    }),
  );
  return counts;
}

// A signal sets this flag and ends any wait in progress: the cycle checks it between steps, starts
// nothing new, closes the database and exits non-zero.
let shuttingDown = false;
let stopSignal = null;
const SIGNAL_EXIT = { SIGINT: 130, SIGTERM: 143 };
const sleepers = new Set();

function shutdown(signal) {
  shuttingDown = true;
  stopSignal ??= signal;
  for (const wake of [...sleepers]) wake();
}

// Waits `ms` milliseconds, or less when a signal arrives; returns at once once shutting down.
function interruptibleSleep(ms) {
  if (shuttingDown || !(ms > 0)) return Promise.resolve();
  return new Promise((resolve) => {
    const wake = () => {
      clearTimeout(timer);
      sleepers.delete(wake);
      resolve();
    };
    const timer = setTimeout(wake, ms);
    sleepers.add(wake);
  });
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

function cycleError(event, err, extra = {}) {
  console.error(JSON.stringify({ at: new Date().toISOString(), event, ...extra, error: err?.message ?? String(err) }));
}

function loopCapHit(loop) {
  console.error(JSON.stringify({ at: new Date().toISOString(), event: "loop_cap_hit", loop, max_iterations: MAX_LOOP_ITERATIONS }));
}

// Title cleanup, exclusions and role merging; an error is logged and the cycle carries on.
async function cleanupRoles() {
  try {
    await applyTitlesAndExclusions();
    const r = await clusterRoles();
    if (r.changed) console.log(JSON.stringify({ at: new Date().toISOString(), event: "roles_clustered", ...r }));
  } catch (err) {
    cycleError("cleanup_error", err);
  }
}

// One full discovery cycle: run every profile's searches, merge duplicate roles, fill in the details
// of this cycle's new postings and of considered roles (retrying temporary failures once after a
// wait), then recheck every considered role's liveness. Returns the work done: `extracted` counts
// postings whose details were attempted; `pending` those left waiting for the next cycle's try and
// `max_retries` those that ran out of tries.
async function runCycle() {
  const total = { searches: 0, inserted: 0, updated: 0, enriched: 0, extracted: 0, pending: 0, max_retries: 0, liveness_checked: 0 };
  // The cycle's start by the database's clock, which first_seen_at and detail_tried_at also use.
  const [{ t: cycleStart }] = await sql`SELECT now()::text AS t`;

  // Request every search, then claim and run them until none are left. Google Jobs runs inside
  // runSearch, so it reruns every cycle too.
  try {
    await sql`UPDATE searches SET run_requested_at = now()`;
    for (let i = 0; !shuttingDown; i++) {
      if (i >= MAX_LOOP_ITERATIONS) {
        loopCapHit("search");
        break;
      }
      const claimed = await claimSearch();
      if (!claimed) break;
      try {
        const counts = await runSearch(claimed.search, claimed.run);
        total.searches++;
        total.inserted += counts.inserted;
        total.updated += counts.updated;
      } catch (err) {
        cycleError("run_error", err, { search: claimed.search.name });
      }
    }
  } catch (err) {
    cycleError("claim_error", err);
  }

  if (!shuttingDown) await cleanupRoles();

  // Fill every target's details, then follow links from the primaries not fetched yet (which can add
  // new postings to fill), until neither finds work. Details go first so a page is fetched once,
  // not once for links and again for details. An error stops that stage for the rest of the cycle
  // instead of retrying it in a loop.
  const first = newCounts();
  let enrichOn = true;
  let detailsOn = true;
  for (let i = 0; !shuttingDown && (enrichOn || detailsOn); i++) {
    if (i >= MAX_LOOP_ITERATIONS) {
      loopCapHit("details");
      break;
    }
    let n1 = 0;
    let n2 = 0;
    if (detailsOn) {
      const before = first.targets;
      try {
        await drainDetails(cycleStart, null, first);
      } catch (err) {
        detailsOn = false;
        cycleError("details_error", err);
      }
      n2 = first.targets - before;
    }
    if (shuttingDown) break;
    if (enrichOn) {
      try {
        n1 = await enrichCandidates();
        total.enriched += n1;
      } catch (err) {
        enrichOn = false;
        cycleError("enrich_error", err);
      }
    }
    if (!n1 && !n2) break;
  }
  if (first.targets) logDetails("first", first);

  // Postings that failed for a temporary reason this cycle get one more try after a wait; the next
  // cycle's normal pass tries any still pending a third and last time.
  const retry = newCounts();
  if (first.pending && !shuttingDown) {
    console.log(JSON.stringify({ at: new Date().toISOString(), event: "retry_wait", pending: first.pending, delay_ms: RETRY_DELAY_MS }));
    await interruptibleSleep(RETRY_DELAY_MS);
    if (!shuttingDown) {
      try {
        const [{ t: retryFrom }] = await sql`SELECT now()::text AS t`;
        await drainDetails(cycleStart, retryFrom, retry);
      } catch (err) {
        cycleError("details_error", err, { pass: "retry" });
      }
      if (retry.targets) logDetails("retry", retry);
    }
  }
  total.extracted = first.targets + retry.targets;
  total.max_retries = first.max_retries + retry.max_retries;
  if (!shuttingDown) {
    // Postings tried this cycle that still wait for the next cycle's try.
    const [{ n }] = await sql`SELECT count(*)::int AS n FROM candidates WHERE detail_pending AND detail_tried_at >= ${cycleStart}::timestamptz`;
    total.pending = n;
  }

  // Final cleanup: newly found employers and page text change which postings merge into one role.
  if (!shuttingDown) await cleanupRoles();

  // Every considered role whose primary posting was not checked in the last day.
  for (let i = 0; !shuttingDown; i++) {
    if (i >= MAX_LOOP_ITERATIONS) {
      loopCapHit("liveness");
      break;
    }
    try {
      const n = await checkLiveness();
      if (!n) break;
      total.liveness_checked += n;
    } catch (err) {
      cycleError("liveness_error", err);
      break;
    }
  }

  return total;
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
      SELECT lower(coalesce(found_detail(job_title), title)) AS title, count(*)::int AS n,
             array_agg(canonical_url ORDER BY canonical_url) AS urls
      FROM candidates
      GROUP BY lower(coalesce(found_detail(job_title), title))
      HAVING count(*) > 1
      ORDER BY count(*) DESC
      LIMIT 15
    `;
    const [blanks] = await sql`
      SELECT count(*)::int AS roles,
             count(*) FILTER (WHERE coalesce(domain, '') = '')::int AS blank_domain,
             count(*) FILTER (WHERE coalesce(url, '') = '')::int AS blank_url,
             count(*) FILTER (WHERE found_detail(employer) IS NULL)::int AS blank_employer,
             count(*) FILTER (WHERE found_detail(skills) IS NULL)::int AS blank_skills,
             count(*) FILTER (WHERE found_detail(success) IS NULL)::int AS blank_success,
             count(*) FILTER (WHERE found_detail(remote_eligibility) IS NULL)::int AS unknown_remote,
             count(*) FILTER (WHERE considered)::int AS considered,
             (array_agg(url) FILTER (WHERE coalesce(domain, '') = ''))[1:5] AS blank_domain_samples
      FROM roles
    `;
    console.log(JSON.stringify({ at: new Date().toISOString(), event: "duplicate_report", ...totals, blanks, groups: groups.slice(0, 5) }));
  } catch (err) {
    console.error(JSON.stringify({ at: new Date().toISOString(), event: "duplicate_report_error", error: err.message }));
  }
}

// Runs one cycle and returns the process exit code: 0 when it finished, non-zero when a signal cut it short.
async function main() {
  const started = Date.now();
  await migrate();
  try {
    await sql.unsafe(READ_ROLE_GRANTS);
  } catch (err) {
    cycleError("grant_error", err);
  }
  await seed();
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "startup", seed_version: SEED_VERSION }));
  await applyTitlesAndExclusions();
  await logDuplicateReport();
  const total = shuttingDown ? null : await runCycle();
  if (total && !shuttingDown) {
    const seconds = Math.round((Date.now() - started) / 1000);
    console.log(JSON.stringify({ at: new Date().toISOString(), event: "cycle_done", ...total, seconds }));
  }
  await sql.close();
  console.log(JSON.stringify({ at: new Date().toISOString(), event: "shutdown", signal: stopSignal ?? undefined }));
  return shuttingDown ? SIGNAL_EXIT[stopSignal] ?? 1 : 0;
}

let exitCode = 1;
try {
  exitCode = await main();
} catch (err) {
  cycleError("fatal", err);
}
process.exit(exitCode);
