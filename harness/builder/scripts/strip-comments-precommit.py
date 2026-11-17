#!/usr/bin/env python3
import difflib
import json
import os
import re
import subprocess
import sys

from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer_for_filename
from pygments.token import Comment
from pygments.util import ClassNotFound

AGENT_PROMPT_TAG = "<!-- agent-prompt -->"

KEEP_DIRECTIVE = "strip-comments:keep"

YAML_BLOCK_SCALAR_RE = re.compile(
    r'^(?P<indent>[ \t]*)(?:-\s+)?'
    r'(?P<key>"[^"]*"|\'[^\']*\'|[^:\s][^:]*)'
    r'\s*:\s*[|>][+-]?[ \t]*(?:#.*)?$'
)

HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")

HEREDOC_TARGET_RE = re.compile(r">\s*([^\s<>|&;]+)\s*<<")

HEREDOC_COMMAND_SPLIT_RE = re.compile(r"[|&;()]")

SHEBANG_RE = re.compile(r"^#!\s*(\S+)(?:\s+(\S+))?")

HUNK_HEADER_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

SHEBANG_ALIASES = {
    "node": "javascript", "nodejs": "javascript",
    "dash": "bash", "ash": "bash",
}

SHELL_LEXER_ALIASES = {"bash", "sh", "ksh", "zsh", "shell", "shell-session", "console"}

SHELL_INTERPRETERS = {"sh", "bash", "dash", "ash", "ksh", "zsh"}

SHELL_BLOCK_KEYS = {"run", "script", "cmd", "command", "commands", "entrypoint"}

SHELL_HINT_RE = re.compile(r'^[ \t]*(?:-[ \t]+)?shell[ \t]*:[ \t]*["\']?([\w.+-]+)')

LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]")

SHELL_HINT_ALIASES = {"pwsh": "powershell", "cmd": "batch", "sh": "bash"}

USAGE = """usage: strip-comments-precommit.py [--full] [--report]

With no arguments, runs as a PreToolUse hook and reads a hook payload on stdin.

  --full      process every tracked file in the working tree, not only the
              lines staged for commit
  --report    write nothing; print a git-appliable patch of what would change
  -h, --help  print this message
"""


def log(msg):
    print(f"strip-comments-precommit: {msg}", file=sys.stderr)


def run(args, cwd, timeout=15):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def allow(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def unstage(root, rel_path, head_exists):
    if head_exists:
        return run(["git", "restore", "--staged", "--", rel_path], root)
    return run(["git", "rm", "--cached", "-q", "--", rel_path], root)


def has_agent_prompt_tag(content):
    lines = content.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                start = i + 1
                break
        else:
            start = 0
    checked = 0
    for line in lines[start:]:
        if line.strip() == "":
            continue
        if line.strip() == AGENT_PROMPT_TAG:
            return True
        checked += 1
        if checked >= 10:
            break
    return False


def is_md_allowlisted(rel_path, content):
    rel_path = rel_path.replace(os.sep, "/")
    if os.path.basename(rel_path) == "README.md":
        return True
    return has_agent_prompt_tag(content)


def added_line_numbers(diff_text):
    added = set()
    for line in diff_text.splitlines():
        m = HUNK_HEADER_RE.match(line)
        if not m:
            continue
        start = int(m.group("new_start"))
        count = int(m.group("new_count")) if m.group("new_count") is not None else 1
        added.update(range(start, start + count))
    return added


def git_added_lines(root, rel_path):
    r = run(["git", "diff", "--cached", "-U0", "--", rel_path], root)
    if r.returncode != 0:
        return set()
    return added_line_numbers(r.stdout)


def read_index_blob(root, rel_path):
    r = subprocess.run(
        ["git", "show", f":{rel_path}"], cwd=root,
        capture_output=True, timeout=15,
    )
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def get_file_mode(root, rel_path):
    r = run(["git", "ls-files", "-s", "--", rel_path], root)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[0]


def write_index_blob(root, rel_path, new_content, mode):
    h = subprocess.run(
        ["git", "hash-object", "-w", "--path", rel_path, "--stdin"],
        cwd=root, input=new_content.encode("utf-8"),
        capture_output=True, timeout=15,
    )
    if h.returncode != 0:
        return False, f"hash-object failed: {h.stderr.decode('utf-8', 'replace').strip()}"
    sha = h.stdout.decode("utf-8").strip()
    u = run(["git", "update-index", "--cacheinfo", f"{mode},{sha},{rel_path}"], root)
    if u.returncode != 0:
        return False, f"update-index failed: {u.stderr.strip()}"
    return True, sha


def resolve_lexer(rel_path, content):
    first_line = content.split("\n", 1)[0]
    if first_line.lstrip().startswith("#!"):
        shebang_lexer = lexer_from_shebang(first_line)
        if shebang_lexer is not None:
            return shebang_lexer
    try:
        return get_lexer_for_filename(rel_path, content)
    except ClassNotFound:
        pass
    except Exception:
        pass
    try:
        return guess_lexer_for_filename(rel_path, content)
    except ClassNotFound:
        return None
    except Exception:
        return None


def lexer_for_hint(hint, code=None):
    if not hint:
        return None
    try:
        return get_lexer_for_filename(hint, code)
    except ClassNotFound:
        return None
    except Exception:
        return None


def lexer_from_shebang(first_line):
    m = SHEBANG_RE.match(first_line.lstrip())
    if not m:
        return None
    interp, arg = m.group(1), m.group(2)
    name = os.path.basename(interp)
    if name == "env" and arg:
        name = os.path.basename(arg)
    for candidate in (name, SHEBANG_ALIASES.get(name)):
        if not candidate:
            continue
        try:
            return get_lexer_by_name(candidate)
        except ClassNotFound:
            continue
    return None


def interpreter_name(first_line):
    m = SHEBANG_RE.match(first_line.lstrip())
    if not m:
        return ""
    name = os.path.basename(m.group(1))
    if name == "env" and m.group(2):
        name = os.path.basename(m.group(2))
    return name


def is_comment_token(ttype):
    if ttype not in Comment:
        return False
    return not (ttype in Comment.Preproc or ttype in Comment.PreprocFile)


def is_shell_lexer(lexer):
    aliases = set(getattr(lexer, "aliases", None) or [])
    return bool(aliases & SHELL_LEXER_ALIASES)


def demote_inline_hash_comments(segs_by_line):
    for parts in segs_by_line.values():
        prev_char = ""
        for i, (text, is_comment) in enumerate(parts):
            if is_comment and prev_char and prev_char not in " \t":
                parts[i] = (text, False)
            prev_char = text[-1]


def line_segments(content, lexer):
    lines_src = content.splitlines()
    n = len(lines_src)
    if n == 0:
        return None
    segs = {ln: [] for ln in range(1, n + 1)}
    try:
        tokens = lexer.get_tokens(content)
    except Exception:
        return None
    lineno = 1
    for ttype, value in tokens:
        if not value:
            continue
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                lineno += 1
            if not part or lineno > n:
                continue
            segs[lineno].append((part, is_comment_token(ttype)))
    return segs


def line_plan(segs):
    if not segs:
        return None
    offsets = []
    pos = 0
    for text, _ in segs:
        offsets.append(pos)
        pos += len(text)
    non_ws_idx = [i for i, (t, _) in enumerate(segs) if t.strip()]
    if not non_ws_idx:
        return None
    if all(segs[i][1] for i in non_ws_idx):
        return ("delete", None)
    if all(not segs[i][1] for i in non_ws_idx):
        return None
    last_code_i = max(i for i in non_ws_idx if not segs[i][1])
    if last_code_i == non_ws_idx[-1]:
        return None
    cut = offsets[last_code_i] + len(segs[last_code_i][0])
    return ("partial", cut)


def compute_line_actions(content, lexer):
    lines_src = content.splitlines()
    n = len(lines_src)
    if n == 0:
        return set(), {}
    segs_by_line = line_segments(content, lexer)
    if segs_by_line is None:
        return set(), {}
    if is_shell_lexer(lexer):
        demote_inline_hash_comments(segs_by_line)
    shebang = lines_src[0].lstrip().startswith("#!")
    delete = set()
    partial = {}
    for ln in range(1, n + 1):
        if ln == 1 and shebang:
            continue
        plan = line_plan(segs_by_line.get(ln, []))
        if plan is None:
            continue
        kind, cut = plan
        if kind == "delete":
            delete.add(ln)
        else:
            partial[ln] = cut
    return delete, partial


def find_yaml_block_scalars(lines_src):
    results = []
    n = len(lines_src)
    i = 0
    while i < n:
        m = YAML_BLOCK_SCALAR_RE.match(lines_src[i])
        if not m:
            i += 1
            continue
        header_indent = len(m.group("indent").expandtabs())
        key = m.group("key").strip().strip("\"'")
        body_idx = []
        j = i + 1
        while j < n:
            candidate = lines_src[j]
            if candidate.strip() == "":
                body_idx.append(j)
                j += 1
                continue
            indent = len(candidate) - len(candidate.lstrip(" \t"))
            if indent <= header_indent:
                break
            body_idx.append(j)
            j += 1
        if body_idx:
            results.append({
                "header_line": i + 1,
                "header_indent": header_indent,
                "key": key,
                "body_lines_idx": body_idx,
            })
        i = j if body_idx else i + 1
    return results


def block_scope_lines(lines_src, header_idx, header_indent, body_idx):
    n = len(lines_src)
    scope = []
    i = header_idx - 1
    while i >= 0:
        line = lines_src[i]
        if line.strip():
            indent = len(line) - len(line.lstrip(" \t"))
            if indent < header_indent:
                break
            scope.append(line)
            if indent == header_indent and LIST_ITEM_RE.match(line):
                break
        i -= 1
    j = (body_idx[-1] + 1) if body_idx else header_idx + 1
    while j < n:
        line = lines_src[j]
        if line.strip():
            indent = len(line) - len(line.lstrip(" \t"))
            if indent < header_indent or (indent == header_indent and LIST_ITEM_RE.match(line)):
                break
            scope.append(line)
        j += 1
    return scope


def declared_shell_lexer(scope_lines):
    for line in scope_lines:
        m = SHELL_HINT_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        for candidate in (SHELL_HINT_ALIASES.get(name), name):
            if not candidate:
                continue
            try:
                return get_lexer_by_name(candidate)
            except ClassNotFound:
                continue
        return None
    return None


def script_block_lexer(lines_src, blk, first_nonblank):
    scope = block_scope_lines(
        lines_src, blk["header_line"] - 1, blk["header_indent"], blk["body_lines_idx"]
    )
    declared = declared_shell_lexer(scope)
    if declared is not None:
        return declared
    if first_nonblank.lstrip().startswith("#!"):
        from_shebang = lexer_from_shebang(first_nonblank)
        if from_shebang is not None:
            return from_shebang
    try:
        return get_lexer_by_name("bash")
    except ClassNotFound:
        return None


def find_heredocs(lines_src):
    results = []
    n = len(lines_src)
    i = 0
    while i < n:
        m = HEREDOC_RE.search(lines_src[i])
        if not m:
            i += 1
            continue
        strip_tabs = "<<-" in lines_src[i]
        delim = m.group(2)
        opening_text = lines_src[i]
        body_idx = []
        closed = False
        j = i + 1
        while j < n:
            check = lines_src[j].lstrip("\t") if strip_tabs else lines_src[j]
            if check == delim:
                closed = True
                break
            body_idx.append(j)
            j += 1
        if body_idx and closed:
            results.append({
                "opening_text": opening_text,
                "strip_tabs": strip_tabs,
                "body_lines_idx": body_idx,
            })
        i = (j + 1) if j < n else n
    return results


def heredoc_command_lexer(opening_text):
    head = opening_text.split("<<", 1)[0]
    segment = HEREDOC_COMMAND_SPLIT_RE.split(head)[-1]
    for token in segment.split():
        if "=" in token or token.startswith("-"):
            continue
        name = os.path.basename(token)
        for candidate in (name, SHEBANG_ALIASES.get(name)):
            if not candidate:
                continue
            try:
                return get_lexer_by_name(candidate)
            except ClassNotFound:
                continue
        return None
    return None


def embedded_actions(body_idx, body_text_lines, embed_lexer, prefix_len_for):
    body_text = "\n".join(body_text_lines)
    delete_rel, partial_rel = compute_line_actions(body_text, embed_lexer)
    if is_shell_lexer(embed_lexer):
        nested_delete, nested_partial = heredoc_embedded_actions(body_text_lines)
        delete_rel |= nested_delete
        partial_rel.update(nested_partial)
    delete_abs = {body_idx[ln - 1] + 1 for ln in delete_rel if 0 < ln <= len(body_idx)}
    partial_abs = {}
    for ln, cut in partial_rel.items():
        if not (0 < ln <= len(body_idx)):
            continue
        abs_idx = body_idx[ln - 1]
        partial_abs[abs_idx + 1] = prefix_len_for(abs_idx) + cut
    return delete_abs, partial_abs


def yaml_embedded_actions(lines_src, allow_script_keys=False):
    delete = set()
    partial = {}
    for blk in find_yaml_block_scalars(lines_src):
        body_idx = blk["body_lines_idx"]
        indents = [
            len(lines_src[k]) - len(lines_src[k].lstrip(" \t"))
            for k in body_idx if lines_src[k].strip()
        ]
        if not indents:
            continue
        dedent = min(indents)
        body_text_lines = [
            lines_src[k][dedent:] if len(lines_src[k]) >= dedent else ""
            for k in body_idx
        ]
        first_nonblank = next((l for l in body_text_lines if l.strip()), "")
        embed_lexer = lexer_for_hint(blk["key"], "\n".join(body_text_lines))
        if embed_lexer is None and allow_script_keys and blk["key"].lower() in SHELL_BLOCK_KEYS:
            embed_lexer = script_block_lexer(lines_src, blk, first_nonblank)
        if embed_lexer is None and first_nonblank.lstrip().startswith("#!"):
            embed_lexer = lexer_from_shebang(first_nonblank)
        if embed_lexer is None:
            continue
        d, p = embedded_actions(body_idx, body_text_lines, embed_lexer, lambda k, dedent=dedent: dedent)
        delete |= d
        partial.update(p)
    return delete, partial


def heredoc_embedded_actions(lines_src):
    delete = set()
    partial = {}
    for hd in find_heredocs(lines_src):
        body_idx = hd["body_lines_idx"]
        strip_tabs = hd["strip_tabs"]
        body_text_lines = [
            (lines_src[k].lstrip("\t") if strip_tabs else lines_src[k])
            for k in body_idx
        ]
        first_nonblank = next((l for l in body_text_lines if l.strip()), "")
        target_m = HEREDOC_TARGET_RE.search(hd["opening_text"])
        embed_lexer = lexer_for_hint(target_m.group(1) if target_m else None, "\n".join(body_text_lines))
        if embed_lexer is None and first_nonblank.lstrip().startswith("#!"):
            embed_lexer = lexer_from_shebang(first_nonblank)
        if embed_lexer is None:
            embed_lexer = heredoc_command_lexer(hd["opening_text"])
        if embed_lexer is None:
            continue

        def prefix_len(k, strip_tabs=strip_tabs):
            if not strip_tabs:
                return 0
            return len(lines_src[k]) - len(lines_src[k].lstrip("\t"))

        d, p = embedded_actions(body_idx, body_text_lines, embed_lexer, prefix_len)
        delete |= d
        partial.update(p)
    return delete, partial


def syntax_kind(rel_path, content):
    ext = os.path.splitext(rel_path)[1].lower()
    interp = interpreter_name(content.split("\n", 1)[0])
    if ext == ".py" or interp.startswith("python"):
        return "python"
    if ext in (".sh", ".bash", ".zsh", ".ksh") or interp in SHELL_INTERPRETERS:
        return "shell"
    if ext == ".json":
        return "json"
    if ext in (".yml", ".yaml"):
        return "yaml"
    return None


def parses_as(kind, rel_path, content):
    try:
        if kind == "python":
            compile(content, rel_path, "exec")
            return True
        if kind == "json":
            json.loads(content)
            return True
        if kind == "shell":
            checker = ["bash", "-n"]
        else:
            checker = ["yq", "-o=json", "."]
        r = subprocess.run(
            checker, input=content.encode("utf-8"),
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except (SyntaxError, ValueError):
        return False
    except Exception:
        return None


def breaks_syntax(rel_path, before, after):
    kind = syntax_kind(rel_path, before)
    if kind is None:
        return False
    if parses_as(kind, rel_path, after) is not False:
        return False
    return parses_as(kind, rel_path, before) is True


def compute_stripped(rel_path, content, added_lines):
    lexer = resolve_lexer(rel_path, content)
    if lexer is None:
        return None, {}

    lines_src = content.splitlines()
    lines_keep = content.splitlines(keepends=True)
    n = len(lines_src)
    if n == 0:
        return None, {}

    delete, partial = compute_line_actions(content, lexer)
    is_yaml = os.path.splitext(rel_path)[1].lower() in (".yml", ".yaml")
    yd, yp = yaml_embedded_actions(lines_src, allow_script_keys=is_yaml)
    hd, hp = heredoc_embedded_actions(lines_src)
    delete |= yd | hd
    partial.update(yp)
    partial.update(hp)

    delete &= added_lines
    partial = {ln: cut for ln, cut in partial.items() if ln in added_lines}
    if not delete and not partial:
        return None, {}

    final_delete = set()
    final_partial = {}
    stripped = {}

    for ln in delete:
        if not (0 < ln <= n):
            continue
        raw = lines_src[ln - 1]
        if KEEP_DIRECTIVE in raw:
            continue
        if ln == 1 and raw.lstrip().startswith("#!"):
            continue
        final_delete.add(ln)
        stripped[ln] = raw

    for ln, cut in partial.items():
        if ln in final_delete or not (0 < ln <= n):
            continue
        raw = lines_src[ln - 1]
        if KEEP_DIRECTIVE in raw:
            continue
        if ln == 1 and raw.lstrip().startswith("#!"):
            continue
        new_text = raw[:cut].rstrip()
        if new_text == raw:
            continue
        final_partial[ln] = new_text
        stripped[ln] = raw

    if not final_delete and not final_partial:
        return None, {}

    out_lines = []
    for idx, line in enumerate(lines_keep, start=1):
        if idx in final_delete:
            continue
        if idx in final_partial:
            ending = line[len(lines_src[idx - 1]):]
            out_lines.append(final_partial[idx] + ending)
            continue
        out_lines.append(line)
    new_content = "".join(out_lines)
    if breaks_syntax(rel_path, content, new_content):
        log(f"{rel_path}: stripping would leave the file unparseable, keeping it as is")
        return None, {}
    return new_content, stripped


def repo_files(root):
    r = run(["git", "ls-files", "-z"], root)
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.split("\0") if p]


def read_working_file(root, rel_path):
    try:
        with open(os.path.join(root, rel_path), encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def write_working_file(root, rel_path, content):
    with open(os.path.join(root, rel_path), "w", encoding="utf-8", newline="") as f:
        f.write(content)


def format_diff(rel_path, before, after, mode=None):
    if before == after:
        return ""
    before_lines = before.splitlines(keepends=True) if before is not None else []
    after_lines = after.splitlines(keepends=True) if after is not None else []
    fromfile = "/dev/null" if before is None else f"a/{rel_path}"
    tofile = "/dev/null" if after is None else f"b/{rel_path}"
    body = []
    for line in difflib.unified_diff(before_lines, after_lines, fromfile=fromfile, tofile=tofile):
        if line.endswith("\n"):
            body.append(line)
        else:
            body.append(line + "\n\\ No newline at end of file\n")
    if not body:
        return ""
    header = [f"diff --git a/{rel_path} b/{rel_path}"]
    if after is None:
        header.append(f"deleted file mode {mode or '100644'}")
    elif before is None:
        header.append(f"new file mode {mode or '100644'}")
    return "\n".join(header) + "\n" + "".join(body)


def run_cli(full, report):
    top = run(["git", "rev-parse", "--show-toplevel"], os.getcwd())
    root = top.stdout.strip()
    if top.returncode != 0 or not root:
        log("not a git work tree")
        sys.exit(1)

    if full:
        files = repo_files(root)
        read_content = lambda rel: read_working_file(root, rel)
    else:
        staged = run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"], root)
        files = [p for p in staged.stdout.split("\0") if p] if staged.returncode == 0 else []
        read_content = lambda rel: read_index_blob(root, rel)

    diffs = []
    stripped_count = 0
    stripped_files = []
    removed_files = []
    symlinks = []

    for rel_path in files:
        ext = os.path.splitext(rel_path)[1].lower()
        if os.path.islink(os.path.join(root, rel_path)):
            symlinks.append(rel_path)
            continue
        content = read_content(rel_path)
        if content is None:
            continue

        if ext == ".md":
            if is_md_allowlisted(rel_path, content):
                continue
            removed_files.append(rel_path)
            if report:
                diffs.append(format_diff(rel_path, content, None, get_file_mode(root, rel_path)))
            else:
                os.remove(os.path.join(root, rel_path))
            continue

        added_lines = set(range(1, len(content.splitlines()) + 1)) if full else git_added_lines(root, rel_path)
        if not added_lines:
            continue

        new_content, stripped = compute_stripped(rel_path, content, added_lines)
        if not stripped:
            continue

        stripped_count += len(stripped)
        stripped_files.append(rel_path)
        if report:
            diffs.append(format_diff(rel_path, content, new_content))
        else:
            write_working_file(root, rel_path, new_content)

    if report:
        sys.stdout.write("".join(diffs))

    if symlinks:
        log(f"skipped {len(symlinks)} symlink(s): {', '.join(sorted(symlinks))}")
    if stripped_files:
        verb = "would strip" if report else "stripped"
        log(f"{verb} {stripped_count} comment line(s) from {len(stripped_files)} file(s): {', '.join(sorted(stripped_files))}")
    if removed_files:
        verb = "would remove" if report else "removed"
        log(f"{verb} {len(removed_files)} untagged markdown file(s): {', '.join(sorted(removed_files))}")


def hook_main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    if data.get("tool_name") != "Bash":
        return

    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if "git commit" not in command:
        return

    cwd = data.get("cwd") or os.getcwd()

    try:
        top = run(["git", "rev-parse", "--show-toplevel"], cwd)
        root = top.stdout.strip()
        if top.returncode != 0 or not root:
            return
    except Exception as exc:
        log(f"not a git work tree or git unavailable, skipping ({exc})")
        return

    try:
        staged = run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
            root,
        )
        if staged.returncode != 0:
            return
        files = [p for p in staged.stdout.split("\0") if p]
    except Exception as exc:
        log(f"failed to list staged files, skipping (fail open): {exc}")
        return

    try:
        head_exists = run(["git", "rev-parse", "--verify", "-q", "HEAD"], root).returncode == 0
    except Exception:
        head_exists = True

    stripped_files = {}
    write_errors = []
    md_candidates = []

    for rel_path in files:
        ext = os.path.splitext(rel_path)[1].lower()
        try:
            if ext == ".md":
                content = read_index_blob(root, rel_path)
                if content is None:
                    continue
                if not is_md_allowlisted(rel_path, content):
                    md_candidates.append(rel_path)
                continue

            added_lines = git_added_lines(root, rel_path)
            if not added_lines:
                continue

            content = read_index_blob(root, rel_path)
            if content is None:
                continue

            new_content, stripped = compute_stripped(rel_path, content, added_lines)
            if not stripped:
                continue

            mode = get_file_mode(root, rel_path) or "100644"
            ok, info = write_index_blob(root, rel_path, new_content, mode)
            if not ok:
                write_errors.append((rel_path, info))
                continue

            stripped_files[rel_path] = stripped
        except Exception as exc:
            log(f"failed processing {rel_path}, leaving it as staged (fail open): {exc}")
            continue

    if stripped_files:
        for rel_path in sorted(stripped_files):
            for lineno, text in sorted(stripped_files[rel_path].items()):
                log(f"{rel_path}:{lineno}: {text}")

    parts = []

    if write_errors:
        for rel_path, info in write_errors:
            log(f"index update failed for {rel_path}: {info}")
        failed_names = sorted(name for name, _ in write_errors)
        succeeded_names = sorted(stripped_files)
        warning = (
            f"WARNING: comment-stripping failed for {len(write_errors)} "
            f"file(s) ({', '.join(failed_names)}) while writing the stripped "
            "blob back to the staged index — see stderr for the underlying "
            "git error for each. This hook never blocks a commit over its "
            "own failures, so the commit is PROCEEDING WITH COMMENTS STILL "
            f"PRESENT in {', '.join(failed_names)}"
        )
        if succeeded_names:
            warning += (
                f"; {', '.join(succeeded_names)} were stripped successfully "
                "and are unaffected by this"
            )
        parts.append(warning)

    excluded_md = []
    for rel_path in md_candidates:
        restore = unstage(root, rel_path, head_exists)
        if restore.returncode == 0:
            excluded_md.append(rel_path)
        else:
            log(f"unstage failed for {rel_path}: {restore.stderr.strip()}")

    if stripped_files:
        total = sum(len(v) for v in stripped_files.values())
        names = ", ".join(sorted(stripped_files))
        parts.append(
            f"stripped {total} comment line(s) from {len(stripped_files)} "
            f"file(s) ({names}) — working tree unchanged; the post-commit "
            "`git status` diff is expected"
        )
    if excluded_md:
        names = ", ".join(sorted(excluded_md))
        parts.append(
            f"excluded {names} (untagged markdown) — still in your working "
            "tree; agent-facing prompt content is committable once it carries "
            f"a standalone `{AGENT_PROMPT_TAG}` line near the top (after any "
            "frontmatter)"
        )

    if not parts:
        return

    allow("Pre-commit policy (simplesalt/projects#307): " + "; ".join(parts) + ".")


def main():
    argv = sys.argv[1:]
    if not argv:
        hook_main()
        return
    if "-h" in argv or "--help" in argv:
        sys.stdout.write(USAGE)
        return
    unknown = [a for a in argv if a not in ("--full", "--report")]
    if unknown:
        log(f"unrecognised argument(s): {' '.join(unknown)}")
        sys.stderr.write(USAGE)
        sys.exit(2)
    run_cli("--full" in argv, "--report" in argv)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if sys.argv[1:]:
            raise
        log(f"unexpected failure, allowing commit unmodified (fail open): {exc}")
    sys.exit(0)
