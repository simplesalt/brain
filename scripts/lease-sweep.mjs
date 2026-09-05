import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ORG = 'simplesalt';
const BOARD_NUMBER = 3;
const LEASE_FIELD = 'In Use';
const POD_NAMESPACE = process.env.LEASE_SWEEP_NAMESPACE ?? 'ssint-main-coding';
const GITHUB_API = 'https://api.github.com/graphql';
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;

const SA_DIR = '/var/run/secrets/kubernetes.io/serviceaccount';
const K8S_HOST = 'https://kubernetes.default.svc';

let _k8sToken;
function k8sToken() {
  if (!_k8sToken) _k8sToken = readFileSync(join(SA_DIR, 'token'), 'utf8').trim();
  return _k8sToken;
}

async function podExists(name) {
  const r = await fetch(`${K8S_HOST}/api/v1/namespaces/${POD_NAMESPACE}/pods/${name}`, {
    headers: { Authorization: `Bearer ${k8sToken()}` },
  });
  if (r.status === 200) return true;
  if (r.status === 404) return false;
  throw new Error(`k8s GET pods/${name} ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

async function ghGraphql(query, variables) {
  const r = await fetch(GITHUB_API, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ query, variables }),
  });
  const body = await r.json();
  if (body.errors) throw new Error(`GitHub GraphQL error: ${JSON.stringify(body.errors)}`);
  return body.data;
}

async function projectId() {
  const data = await ghGraphql(
    `query($org:String!, $number:Int!) {
      organization(login:$org) { projectV2(number:$number) { id } }
    }`,
    { org: ORG, number: BOARD_NUMBER },
  );
  return data.organization.projectV2.id;
}

async function leaseFieldId() {
  const data = await ghGraphql(
    `query($org:String!, $number:Int!) {
      organization(login:$org) {
        projectV2(number:$number) {
          fields(first:30) { nodes { ... on ProjectV2FieldCommon { id name } } }
        }
      }
    }`,
    { org: ORG, number: BOARD_NUMBER },
  );
  const field = data.organization.projectV2.fields.nodes.find((f) => f.name === LEASE_FIELD);
  if (!field) throw new Error(`no field named '${LEASE_FIELD}' on board ${BOARD_NUMBER}`);
  return field.id;
}

async function leasedItems() {
  const items = [];
  let cursor = null;
  for (;;) {
    const data = await ghGraphql(
      `query($org:String!, $number:Int!, $cursor:String) {
        organization(login:$org) {
          projectV2(number:$number) {
            items(first:100, after:$cursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                content { ... on Issue { number repository { nameWithOwner } } }
                fieldValueByName(name:"In Use") { ... on ProjectV2ItemFieldTextValue { text } }
              }
            }
          }
        }
      }`,
      { org: ORG, number: BOARD_NUMBER, cursor },
    );
    const page = data.organization.projectV2.items;
    for (const node of page.nodes) {
      const holder = node.fieldValueByName?.text;
      const repo = node.content?.repository?.nameWithOwner;
      if (holder && repo) items.push({ itemId: node.id, ref: `${repo}#${node.content.number}`, holder });
    }
    if (!page.pageInfo.hasNextPage) break;
    cursor = page.pageInfo.endCursor;
  }
  return items;
}

async function clearLease(projId, fieldId, itemId) {
  await ghGraphql(
    `mutation($project:ID!, $item:ID!, $field:ID!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project
        itemId: $item
        fieldId: $field
        value: { text: "" }
      }) { projectV2Item { id } }
    }`,
    { project: projId, item: itemId, field: fieldId },
  );
}

async function main() {
  if (!GITHUB_TOKEN) throw new Error('GITHUB_TOKEN is required');
  const items = await leasedItems();
  if (items.length === 0) {
    console.log('no leased efforts found');
    return;
  }
  const projId = await projectId();
  const fieldId = await leaseFieldId();
  let skipped = 0;
  for (const item of items) {
    const podName = item.holder.split('/', 1)[0];
    let exists;
    try {
      exists = await podExists(podName);
    } catch (err) {
      console.warn(`skip ${item.ref}: cannot verify pod ${podName} (${err.message})`, err.cause ?? '');
      skipped++;
      continue;
    }
    if (exists) {
      console.log(`${item.ref}: held by ${item.holder}, pod present, leaving lease`);
      continue;
    }
    await clearLease(projId, fieldId, item.itemId);
    console.log(`${item.ref}: pod ${podName} gone, released lease`);
  }
  if (skipped === items.length) {
    throw new Error(`all ${items.length} leased effort(s) were skipped, no pod could be verified`);
  }
}

await main();
