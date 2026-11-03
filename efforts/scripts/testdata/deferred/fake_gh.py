import json
import os
import re
import subprocess
import sys

REPO = "simplesalt/projects"
BOARD_NUMBER = 3
PROJECT_ID = "PVT_1"
REPO_NODE_ID = "R_1"
STATUSES = ["Planning", "Doing", "Blocked", "Review", "Paused", "Deferred", "Done", "Cancelled"]
ASSIGNEES = [
    "Dylan", "Russ", "ssint-build-basis", "ssint-build-brain",
    "ssint-build-cloud-basis", "ssint-build-oci", "ssint-build-projects",
]
ISSUE_TYPES = ["Feature", "Bug", "Analysis", "Advisory"]
FIELDS = {
    "F_STATUS": ("Status", "single"),
    "F_ASSIGNED": ("Assigned", "single"),
    "F_PRIORITY": ("Priority", "number"),
    "F_INUSE": ("In Use", "text"),
}
OPTIONS = {"Status": STATUSES, "Assigned": ASSIGNEES}

STATE_PATH = os.environ["FAKE_GH_STATE"]
LOG_PATH = os.environ["FAKE_GH_LOG"]


def load():
    with open(STATE_PATH) as handle:
        return json.load(handle)


STATE = load()


def save():
    with open(STATE_PATH, "w") as handle:
        json.dump(STATE, handle)


def log(op, **details):
    with open(LOG_PATH, "a") as handle:
        handle.write(json.dumps({"op": op, **details}) + "\n")


JQ = []


def finish(payload, code=0):
    if JQ:
        proc = subprocess.run(["jq", "-r", JQ[0]], input=json.dumps(payload), capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode or code)
    print(json.dumps(payload))
    sys.exit(code)


def fail(message, code=1):
    sys.stderr.write("gh: " + message + "\n")
    sys.exit(code)


def unhandled(detail):
    log("unhandled", argv=sys.argv[1:], detail=detail)
    fail("the stand-in gh does not handle this call: " + detail, 3)


def typed(value):
    if value == "null":
        return None
    if value in ("true", "false"):
        return value == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_args(argv):
    if not argv or argv[0] != "api":
        unhandled("not an api call")
    opts = {"method": "GET", "fields": {}, "jq": None, "input": False, "endpoint": None}
    rest = argv[1:]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "-X":
            opts["method"] = rest[i + 1]
            i += 2
        elif arg in ("-f", "-F"):
            key, value = rest[i + 1].split("=", 1)
            opts["fields"][key] = value if arg == "-f" else typed(value)
            i += 2
        elif arg == "--jq":
            opts["jq"] = rest[i + 1]
            i += 2
        elif arg == "--input":
            opts["input"] = rest[i + 1]
            i += 2
        elif arg.startswith("-"):
            unhandled("unsupported flag " + arg)
        elif opts["endpoint"] is None:
            opts["endpoint"] = arg
            i += 1
        else:
            unhandled("unexpected argument " + arg)
    return opts


def issue_of(number):
    return STATE["issues"].get(str(number))


def number_of(node_id):
    return node_id.split("_", 1)[1]


def issue_ref(number):
    return {"repository": {"nameWithOwner": REPO}, "number": int(number)}


def children_of(number):
    return [n for n, item in STATE["issues"].items() if item.get("parent") == str(number)]


def field_values(item):
    nodes = []
    if item.get("status"):
        nodes.append({"name": item["status"], "field": {"name": "Status"}})
    if item.get("assigned"):
        nodes.append({"name": item["assigned"], "field": {"name": "Assigned"}})
    if item.get("in_use"):
        nodes.append({"text": item["in_use"], "field": {"name": "In Use"}})
    if item.get("priority") is not None:
        nodes.append({"number": item["priority"], "field": {"name": "Priority"}})
    return nodes


def field_aliases(item):
    return {
        "status": {"name": item["status"]} if item.get("status") else None,
        "assigned": {"name": item["assigned"]} if item.get("assigned") else None,
        "inuse": {"text": item["in_use"]} if item.get("in_use") else None,
        "fieldValueByName": {"number": item["priority"]} if item.get("priority") is not None else None,
    }


def project_item(number, item):
    return {
        "id": "PVTI_" + number,
        "project": {"number": BOARD_NUMBER},
        "fieldValues": {"nodes": field_values(item)},
        **field_aliases(item),
    }


def child_node(number):
    item = STATE["issues"][number]
    return {
        "id": "I_" + number,
        **issue_ref(number),
        "title": item["title"],
        "state": item["state"],
        "projectItems": {"nodes": [project_item(number, item)]},
    }


def issue_node(number):
    item = STATE["issues"][number]
    parent = item.get("parent")
    return {
        "id": "I_" + number,
        "number": int(number),
        "title": item["title"],
        "url": f"https://github.com/{REPO}/issues/{number}",
        "state": item["state"],
        "stateReason": item.get("state_reason"),
        "body": item["body"],
        "repository": {"nameWithOwner": REPO},
        "parent": {"id": "I_" + parent, **issue_ref(parent)} if parent else None,
        "blockedBy": {"nodes": [
            {"id": "I_" + b, **issue_ref(b), "state": STATE["issues"][b]["state"]}
            for b in item["blocked_by"]
        ]},
        "subIssues": {"nodes": [child_node(c) for c in children_of(number)]},
        "projectItems": {"nodes": [project_item(number, item)]},
    }


def board_items():
    nodes = []
    for number, item in STATE["issues"].items():
        node = issue_node(number)
        nodes.append({
            "id": "PVTI_" + number,
            "content": {
                "__typename": "Issue",
                "id": node["id"],
                "number": node["number"],
                "title": node["title"],
                "url": node["url"],
                "state": node["state"],
                "repository": node["repository"],
            },
            **field_aliases(item),
        })
    return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}


def org_payload(**project):
    return {"data": {"organization": {"projectV2": project}}}


def create_issue(variables):
    spec = variables["input"]
    if spec["projectV2Ids"] != [PROJECT_ID] or spec["repositoryId"] != REPO_NODE_ID:
        unhandled("createIssue against an unknown repository or project")
    number = str(STATE["next_number"])
    STATE["next_number"] += 1
    parent = spec.get("parentIssueId")
    STATE["issues"][number] = {
        "title": spec["title"], "state": "OPEN", "state_reason": None, "body": spec["body"],
        "parent": number_of(parent) if parent else None, "blocked_by": [],
        "status": "", "priority": None, "in_use": "", "assigned": "",
        "type": spec["issueTypeId"].split("_", 1)[1],
    }
    save()
    log("create_issue", number=int(number), title=spec["title"], parent=int(number_of(parent)) if parent else None)
    finish({"data": {"createIssue": {"issue": {
        "id": "I_" + number, "number": int(number),
        "url": f"https://github.com/{REPO}/issues/{number}", "title": spec["title"],
    }}}})


def change_blocker(query, variables):
    adding = "addBlockedBy(" in query
    blocked = number_of(variables["issueId"])
    blocker = number_of(variables["blockingIssueId"])
    item = issue_of(blocked)
    if item is None or issue_of(blocker) is None:
        fail("Could not resolve to an issue")
    if adding and blocker not in item["blocked_by"]:
        item["blocked_by"].append(blocker)
    if not adding:
        item["blocked_by"] = [b for b in item["blocked_by"] if b != blocker]
    save()
    log("add_blocked_by" if adding else "remove_blocked_by", issue=int(blocked), blocker=int(blocker))
    finish({"data": {"addBlockedBy" if adding else "removeBlockedBy": {"clientMutationId": None}}})


def set_field(query, variables):
    if variables.get("project") != PROJECT_ID:
        fail("Could not resolve to a ProjectV2")
    number = number_of(variables["item"])
    item = issue_of(number)
    field = FIELDS.get(variables.get("field"))
    if item is None or field is None:
        fail("Could not resolve the project item or field")
    name, kind = field
    if "singleSelectOptionId" in query:
        sent = "single"
        value = variables["option"].removeprefix("O_")
        if value not in OPTIONS.get(name, []):
            fail("the option does not belong to the field " + name)
    elif "number:" in query:
        sent = "number"
        value = variables["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail("the value is not a number")
    elif "text:" in query:
        sent = "text"
        value = variables["value"]
    else:
        unhandled("unknown field mutation")
    if sent != kind:
        fail(f"the field {name} is a {kind} field, not a {sent} field")
    key = {"Status": "status", "Assigned": "assigned", "Priority": "priority", "In Use": "in_use"}[name]
    item[key] = value
    save()
    log("set_field", item=int(number), field=name, value=value)
    finish({"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}})


def repository_issue(variables):
    if f"{variables.get('owner')}/{variables.get('repo')}" != REPO or issue_of(variables["number"]) is None:
        finish({"data": {"repository": {"issue": None}}, "errors": [{"message": "Could not resolve to an Issue"}]}, code=1)
    log("read_issue", number=variables["number"])
    finish({"data": {"repository": {"issue": issue_node(str(variables["number"]))}}})


def single_node(variables):
    number = number_of(variables["id"])
    if issue_of(number) is None:
        finish({"data": {"node": None}})
    log("read_node", number=int(number))
    finish({"data": {"node": issue_node(number)}})


def aliased_nodes(query, variables):
    data = {}
    for alias, var in re.findall(r"\b(i\d+): node\(id:\$(id\d+)\)", query):
        number = number_of(variables[var])
        data[alias] = issue_node(number) if issue_of(number) else None
    log("read_nodes", numbers=sorted(int(n["number"]) for n in data.values() if n))
    finish({"data": data})


def graphql(query, variables):
    q = " ".join(query.split())
    if "createIssue(input" in q:
        create_issue(variables)
    if "addBlockedBy(" in q or "removeBlockedBy(" in q:
        change_blocker(q, variables)
    if "updateProjectV2ItemFieldValue(" in q:
        set_field(q, variables)
    if "issueTypes(first:20)" in q:
        finish({"data": {"organization": {"issueTypes": {"nodes": [{"id": "IT_" + t, "name": t} for t in ISSUE_TYPES]}}}})
    if "projectV2(number:$number) { id }" in q:
        finish(org_payload(id=PROJECT_ID))
    if "ProjectV2SingleSelectField" in q:
        nodes = [
            {"id": fid, "name": name, "options": [{"id": "O_" + o, "name": o} for o in OPTIONS[name]]} if kind == "single" else {}
            for fid, (name, kind) in FIELDS.items()
        ]
        finish(org_payload(fields={"nodes": nodes}))
    if "ProjectV2FieldCommon { id name }" in q:
        finish(org_payload(fields={"nodes": [{"id": fid, "name": name} for fid, (name, _) in FIELDS.items()]}))
    if "fields(first:50)" in q:
        finish(org_payload(fields={"nodes": [{"name": "Title"}] + [{"name": name} for name, _ in FIELDS.values()]}))
    if "items(first:100, after:" in q:
        finish(org_payload(items=board_items()))
    if re.search(r"\bi\d+: node\(id:\$id\d+\)", q):
        aliased_nodes(q, variables)
    if "repository(owner:$owner, name:$repo) { issue(number:$number)" in q:
        repository_issue(variables)
    if "node(id:$id) { ... on Issue {" in q:
        single_node(variables)
    unhandled("graphql query: " + q[:100])


def patch_issue(number, fields):
    item = issue_of(number)
    if item is None:
        fail("Not Found (HTTP 404)")
    if not set(fields) <= {"body", "state", "state_reason"}:
        unhandled("PATCH with fields " + ",".join(sorted(fields)))
    if "body" in fields:
        item["body"] = fields["body"]
    if "state" in fields:
        item["state"] = fields["state"].upper()
        item["state_reason"] = fields["state_reason"].upper() if item["state"] == "CLOSED" and "state_reason" in fields else None
    save()
    log("patch_issue", number=int(number), fields=fields)
    finish({"number": int(number)})


def rest(opts):
    endpoint = opts["endpoint"]
    match = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/(\d+)", endpoint)
    if match and match.group(1) == REPO:
        number = match.group(2)
        if opts["method"] == "PATCH":
            patch_issue(number, opts["fields"])
        if opts["method"] == "GET":
            item = issue_of(number)
            if item is None:
                fail("Not Found (HTTP 404)")
            log("read_issue_id", number=int(number))
            finish({"node_id": "I_" + number, "number": int(number), "state": item["state"]})
    if re.fullmatch(r"repos/[^/]+/[^/]+", endpoint) and endpoint[len("repos/"):] == REPO and opts["method"] == "GET":
        log("read_repo_id")
        finish({"node_id": REPO_NODE_ID})
    unhandled(f"{opts['method']} {endpoint}")


def main():
    opts = parse_args(sys.argv[1:])
    if opts["jq"]:
        JQ.append(opts["jq"])
    if opts["endpoint"] != "graphql":
        rest(opts)
    if opts["input"]:
        payload = json.load(sys.stdin)
        query, variables = payload["query"], payload.get("variables", {})
    else:
        variables = dict(opts["fields"])
        query = variables.pop("query")
    graphql(query, variables)


main()
