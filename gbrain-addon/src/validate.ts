import { LOOP_TYPES, type LoopEvidence, type LoopType } from './gbrain-internal.ts';

export const CHANNELS = ['fireflies', 'linkedin', 'sms', 'manual'] as const;
export type Channel = (typeof CHANNELS)[number];

export class AddonParamError extends Error {
  constructor(job: string, msg: string) {
    super(`${job}: ${msg}`);
    this.name = 'AddonParamError';
  }
}

type Rec = Record<string, unknown>;

export function asRecord(job: string, v: unknown): Rec {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new AddonParamError(job, 'params must be an object');
  return v as Rec;
}

export function reqString(job: string, o: Rec, k: string, max = 2000): string {
  const v = o[k];
  if (typeof v !== 'string' || v.trim() === '') throw new AddonParamError(job, `"${k}" must be a non-empty string`);
  if (v.length > max) throw new AddonParamError(job, `"${k}" is longer than ${max} characters`);
  return v.trim();
}

export function optString(job: string, o: Rec, k: string, max = 2000): string | undefined {
  if (o[k] === undefined || o[k] === null) return undefined;
  return reqString(job, o, k, max);
}

export function sourceId(job: string, o: Rec): string {
  const v = reqString(job, o, 'source_id', 64);
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(v)) throw new AddonParamError(job, `"source_id" is not a valid source id: ${v}`);
  return v;
}

export function channel(job: string, v: unknown, field = 'channel'): Channel {
  if (typeof v !== 'string' || !(CHANNELS as readonly string[]).includes(v)) {
    throw new AddonParamError(job, `"${field}" must be one of ${CHANNELS.join(', ')}`);
  }
  return v as Channel;
}

export function loopType(job: string, v: unknown): LoopType {
  if (typeof v !== 'string' || !(LOOP_TYPES as readonly string[]).includes(v)) {
    throw new AddonParamError(job, `"loop_type" must be one of ${LOOP_TYPES.join(', ')}`);
  }
  return v as LoopType;
}

/** Validate an ISO timestamp; returns epoch ms and the normalized ISO string. */
export function isoDate(job: string, v: unknown, field: string): { ms: number; iso: string } {
  if (typeof v !== 'string' || v.trim() === '') throw new AddonParamError(job, `"${field}" must be an ISO date string`);
  const ms = Date.parse(v);
  if (!Number.isFinite(ms)) throw new AddonParamError(job, `"${field}" is not a valid date: ${v}`);
  return { ms, iso: new Date(ms).toISOString() };
}

export function evidence(job: string, v: unknown): LoopEvidence[] {
  if (!Array.isArray(v)) throw new AddonParamError(job, '"evidence" must be an array');
  return v.map((e, i) => {
    if (!e || typeof e !== 'object' || Array.isArray(e)) throw new AddonParamError(job, `evidence[${i}] must be an object`);
    const r = e as Rec;
    const out: LoopEvidence = {};
    for (const k of ['message_id', 'page_slug', 'quote'] as const) {
      if (r[k] === undefined || r[k] === null) continue;
      if (typeof r[k] !== 'string') throw new AddonParamError(job, `evidence[${i}].${k} must be a string`);
      out[k] = k === 'quote' ? (r[k] as string).slice(0, 500) : (r[k] as string);
    }
    return out;
  });
}
