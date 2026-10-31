#!/usr/bin/env bun
import { SQL } from "bun";

const DATABASE_URL = process.env.DATABASE_URL;
const NOTION_TOKEN = process.env.NOTION_TOKEN;
const JOBS_DATA_SOURCE_ID = process.env.JOBS_DATA_SOURCE_ID;
const TARGET_ROLES_DATA_SOURCE_ID = process.env.TARGET_ROLES_DATA_SOURCE_ID;
const DRY_RUN = ["1", "true"].includes(String(process.env.DRY_RUN ?? "").trim().toLowerCase());
const NOTION_VERSION = "2025-09-03";
const NOTION_API = "https://api.notion.com/v1";
// Searches that no longer run; their roles are never sent to Notion under these profiles.
const RETIRED_PROFILES = ["chad", "mario"];
const MIN_CALL_GAP_MS = 350; // at most 3 Notion calls a second
const MAX_429_RETRIES = 5;
const MAX_LOOP_ITERATIONS = 1000;

// Same blank rule as found() in discovery-worker.mjs and found_detail() in SQL.
const NOT_MENTIONED = "not mentioned";
const CANT_FIND = "can't find";
function found(v) {
  if (v === null || v === undefined) return null;
  const s = String(v);
  if (!s.trim() || s === "unknown" || s === NOT_MENTIONED || s.startsWith(CANT_FIND)) return null;
  return s.trim();
}

function log(event, fields = {}) {
  console.log(JSON.stringify({ at: new Date().toISOString(), event, ...fields }));
}

class NotionError extends Error {
  constructor(status, body) {
    super(`notion ${status}`);
    this.status = status;
    this.body = body;
  }
}

let lastCallAt = 0;
async function notion(path, body) {
  for (let attempt = 0; ; attempt++) {
    const wait = lastCallAt + MIN_CALL_GAP_MS - Date.now();
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    lastCallAt = Date.now();
    const res = await fetch(`${NOTION_API}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${NOTION_TOKEN}`,
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (res.status === 429 && attempt < MAX_429_RETRIES) {
      const secs = Number(res.headers.get("retry-after"));
      const delay = (Number.isFinite(secs) && secs > 0 ? secs : 2) * 1000;
      log("notion_rate_limited", { path, retry_after_ms: delay });
      await new Promise((r) => setTimeout(r, delay));
      continue;
    }
    const text = await res.text();
    if (!res.ok) throw new NotionError(res.status, text.slice(0, 500));
    return text ? JSON.parse(text) : {};
  }
}

async function queryAll(dataSourceId) {
  const pages = [];
  let cursor;
  for (let i = 0; i < MAX_LOOP_ITERATIONS; i++) {
    const out = await notion(`/data_sources/${dataSourceId}/query`, {
      page_size: 100,
      ...(cursor ? { start_cursor: cursor } : {}),
    });
    pages.push(...(out.results ?? []));
    if (!out.has_more || !out.next_cursor) return pages;
    cursor = out.next_cursor;
  }
  log("loop_cap_hit", { data_source_id: dataSourceId });
  return pages;
}

// Plain text of a property, whatever its type: rich_text, title, select or number.
function propText(prop) {
  if (!prop) return "";
  const parts = (arr) => (Array.isArray(arr) ? arr.map((t) => t.plain_text ?? "").join("") : "");
  if (prop.type === "rich_text") return parts(prop.rich_text).trim();
  if (prop.type === "title") return parts(prop.title).trim();
  if (prop.type === "select") return (prop.select?.name ?? "").trim();
  if (prop.type === "number") return prop.number === null || prop.number === undefined ? "" : String(prop.number);
  return "";
}

async function readOrDie(label, dataSourceId) {
  try {
    return await queryAll(dataSourceId);
  } catch (e) {
    log("error", {
      stage: "preflight",
      database: label,
      data_source_id: dataSourceId,
      status: e instanceof NotionError ? e.status : null,
      message: e instanceof NotionError ? e.body : String(e?.message ?? e),
    });
    process.exit(1);
  }
}

function richText(s) {
  return [{ type: "text", text: { content: s.slice(0, 2000) } }];
}

async function main() {
  for (const [k, v] of Object.entries({ DATABASE_URL, NOTION_TOKEN, JOBS_DATA_SOURCE_ID, TARGET_ROLES_DATA_SOURCE_ID })) {
    if (!v) {
      log("error", { stage: "config", message: `${k} is not set` });
      process.exit(1);
    }
  }

  const targetPages = await readOrDie("Target role", TARGET_ROLES_DATA_SOURCE_ID);
  const targetByProfile = new Map();
  for (const p of targetPages) {
    const name = propText(p.properties?.["Search profile"]).toLowerCase();
    if (name) targetByProfile.set(name, p.id);
  }
  log("target_roles_loaded", { pages: targetPages.length, profiles: [...targetByProfile.keys()] });

  const jobPages = await readOrDie("Jobs", JOBS_DATA_SOURCE_ID);
  const existing = new Set();
  for (const p of jobPages) {
    const id = propText(p.properties?.["Crawl ID"]);
    if (id) existing.add(id);
  }
  log("jobs_loaded", { pages: jobPages.length, with_crawl_id: existing.size });

  const sql = new SQL(DATABASE_URL);
  let rows;
  try {
    rows = await sql`SELECT r.role_id, r.job_title, r.employer, r.url,
        r.profiles,
        (SELECT min(o.first_seen_at) FROM candidates o WHERE o.role_id = r.role_id) AS first_seen
      FROM roles r WHERE r.considered ORDER BY r.role_id`;
  } finally {
    await sql.close();
  }
  log("roles_loaded", { considered: rows.length });

  let created = 0;
  let skippedExisting = 0;
  let skippedNoTarget = 0;
  let failed = 0;

  for (const r of rows) {
    const crawlId = String(r.role_id);
    if (existing.has(crawlId)) {
      skippedExisting++;
      continue;
    }
    const profiles = String(r.profiles ?? "")
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter((s) => s && !RETIRED_PROFILES.includes(s));
    const targetIds = [...new Set(profiles.map((p) => targetByProfile.get(p)).filter(Boolean))];
    if (targetIds.length === 0) {
      skippedNoTarget++;
      continue;
    }

    const properties = {
      Name: { title: richText(found(r.job_title) ?? "Untitled role") },
      "Crawl ID": { rich_text: richText(crawlId) },
      Stage: { select: { name: "New" } },
      Source: { select: { name: "Discovery" } },
      "Target role": { relation: targetIds.map((id) => ({ id })) },
    };
    const employer = found(r.employer);
    if (employer) properties.Employer = { rich_text: richText(employer) };
    if (r.url) properties["ATS link"] = { url: String(r.url) };
    if (r.first_seen) {
      const d = new Date(r.first_seen);
      if (!Number.isNaN(d.getTime())) properties["Date found"] = { date: { start: d.toISOString().slice(0, 10) } };
    }

    if (DRY_RUN) {
      log("would_create", { crawl_id: crawlId, name: properties.Name.title[0].text.content, targets: targetIds.length });
      existing.add(crawlId);
      created++;
      continue;
    }
    try {
      await notion("/pages", {
        parent: { type: "data_source_id", data_source_id: JOBS_DATA_SOURCE_ID },
        properties,
      });
      existing.add(crawlId);
      created++;
      log("created", { crawl_id: crawlId });
    } catch (e) {
      failed++;
      log("create_failed", {
        crawl_id: crawlId,
        status: e instanceof NotionError ? e.status : null,
        message: e instanceof NotionError ? e.body : String(e?.message ?? e),
      });
    }
  }

  log("sync_done", {
    created,
    skipped_existing: skippedExisting,
    skipped_no_target: skippedNoTarget,
    failed,
    considered: rows.length,
    dry_run: DRY_RUN,
  });
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => {
  log("error", { stage: "fatal", message: String(e?.stack ?? e) });
  process.exit(1);
});
