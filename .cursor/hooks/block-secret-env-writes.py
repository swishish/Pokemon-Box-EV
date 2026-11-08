#!/usr/bin/env python3
"""Fail-closed block on writes to secret env files.

Cursor permission hooks fail open unless hooks.json sets failClosed: true.
This script still denies when it cannot prove a write avoids secret env paths,
and exits 0 with {"permission":"deny"} so the agent sees the reason. A crash
before that JSON is covered by failClosed on the hook entry.

Blocked basenames (case-insensitive):
  .env, .env.* (.env.local, .env.example, ...), *.env (secrets.env),
  names containing .env. (prod.env.local), .envrc, .envrc.*

Not blocked: environment.json, src/env.ts, or a README whose text mentions .env.

Gated events (see .cursor/hooks.json):
  preToolUse on write/edit tools, and beforeShellExecution (plus Shell).

Verify, without creating a real env file:

  python3 .cursor/hooks/block-secret-env-writes.py --self-test
  printf '%s' '{"tool_name":"Write","tool_input":{"path":".env","contents":"X=1"}}' \\
    | python3 .cursor/hooks/block-secret-env-writes.py
  printf '%s' '{"tool_name":"Write","tool_input":{"path":"README.md","contents":"ok"}}' \\
    | python3 .cursor/hooks/block-secret-env-writes.py

The first piped call prints "permission": "deny". The second prints "allow".
Shell commands are judged from the command string. A program that writes .env
without naming that path is not visible here. Inline code that names one of
these files is denied, because a read and a write look the same.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys

# Full basename match. "environment" and "foo.environment" do not match.
_SECRET_BASENAME = re.compile(
    r"(?i)^(?:"
    r"\.env(?:\.[A-Za-z0-9_-]+)*"
    r"|\.envrc(?:\.[A-Za-z0-9_-]+)*"
    r"|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.env(?:\.[A-Za-z0-9_-]+)*"
    r"|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.envrc(?:\.[A-Za-z0-9_-]+)*"
    r")$"
)
# Same names as a substring, so inline code and patches can be spotted.
_SECRET_MENTION = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:"
    r"\.env(?:\.[A-Za-z0-9_-]+)*"
    r"|\.envrc(?:\.[A-Za-z0-9_-]+)*"
    r"|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.env(?:\.[A-Za-z0-9_-]+)*"
    r"|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.envrc(?:\.[A-Za-z0-9_-]+)*"
    r")(?![A-Za-z0-9])"
)

_PATH_KEYS = {
    "path",
    "filepath",
    "targetfile",
    "targetnotebook",
    "notebookpath",
    "uri",
    "file",
    "targetpath",
    "relativepath",
    "relativeworkspacepath",
}
_PATCH_KEYS = {
    "patch",
    "diff",
    "patchtext",
    "unifieddiff",
    "input",
}
_SKIP_KEYS = {
    "contents",
    "content",
    "oldstring",
    "newstring",
    "oldstr",
    "newstr",
    "text",
    "body",
    "command",
    "pattern",
    "query",
    "prompt",
    "usermessage",
    "agentmessage",
    "description",
    "workingdirectory",
    "cwd",
}

_READERS = {
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "bat",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "awk",
    "gawk",
    "wc",
    "stat",
    "file",
    "ls",
    "dir",
    "diff",
    "cmp",
    "comm",
    "cut",
    "sort",
    "uniq",
    "nl",
    "od",
    "hexdump",
    "strings",
    "jq",
    "yq",
    "column",
    "fold",
    "tr",
    "rev",
    "tac",
    "type",
    "which",
    "whereis",
    "realpath",
    "readlink",
    "test",
    "[",
    "source",
    ".",
    "sed",
    "perl",
    "ruby",
}
_NON_FILE = {"echo", "printf", "true", "false", ":", "pwd", "whoami", "date"}
_GIT_READ = {
    "diff",
    "show",
    "log",
    "status",
    "blame",
    "grep",
    "ls-files",
    "check-ignore",
    "rev-parse",
    "cat-file",
    "hash-object",
}
_DEST_LAST = {"cp", "mv", "ln", "install", "rsync", "link"}
_WRITE_ANY = {
    "tee",
    "touch",
    "truncate",
    "vim",
    "vi",
    "nvim",
    "nano",
    "emacs",
    "code",
    "codium",
    "hx",
    "micro",
    "pico",
    "ed",
    "ex",
    "set-content",
    "add-content",
    "out-file",
    "new-item",
}
_WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "nice", "stdbuf", "env"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ash", "ksh", "fish"}
_INTERPRETERS = {
    "python",
    "python2",
    "python3",
    "pypy",
    "pypy3",
    "node",
    "nodejs",
    "deno",
    "bun",
    "ruby",
    "perl",
    "php",
}
_INPLACE_CMDS = {"sed", "perl", "ruby", "gawk"}
_FLAG_WITH_ARG = {
    "-t",
    "--target-directory",
    "--target",
    "-m",
    "--mode",
    "-o",
    "--output",
    "--output-document",
    "--out",
    "-g",
    "--group",
    "--owner",
    "-S",
    "--suffix",
    "--context",
    "-u",
    "--user",
    "-s",
    "--size",
    "-e",
    "--eval",
    "-c",
    "-p",
    "--prompt",
}
_SPLIT_TOKENS = {"&&", "||", "|", "|&", "&", ";"}


def _norm_key(key: str) -> str:
    return key.replace("-", "").replace("_", "").lower()


def _basename(path: str) -> str:
    text = path.strip().strip("'\"")
    if text.lower().startswith("file://"):
        text = text[7:]
    text = text.split("?", 1)[0].rstrip("/\\")
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def is_secret_env_path(path: str) -> bool:
    if not path or path in {"/dev/null", "NUL", "nul"}:
        return False
    base = _basename(path)
    if not base or base in {".", ".."}:
        return False
    return _SECRET_BASENAME.fullmatch(base) is not None


def mentions_secret(text: str) -> bool:
    return _SECRET_MENTION.search(text) is not None


def _allow() -> dict:
    return {"permission": "allow"}


def _deny(path: str | None, detail: str) -> dict:
    where = f" ({path})" if path else ""
    if path:
        user = f"Blocked a write to a secret env file{where}."
    else:
        user = "Blocked a write: the secret-env hook could not tell whether it was safe."
    return {
        "permission": "deny",
        "user_message": user,
        "agent_message": (
            "Denied by the fail-closed secret-env hook. Do not create or edit "
            ".env, .env.*, *.env, or .envrc"
            f"{where}. {detail} Ordinary non-secret files are allowed."
        ),
    }


def _base_cmd(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _read_paren(text: str, open_idx: int) -> tuple[str, int] | None:
    """Return (inner, index_after_close) for text[open_idx] == '('."""
    depth = 0
    quote = None
    i = open_idx
    while i < len(text):
        char = text[i]
        if quote is None and char == "\\" and i + 1 < len(text):
            i += 2
            continue
        if quote is None and char in {"'", '"'}:
            quote = char
        elif quote == char:
            quote = None
        elif quote is None and char == "(":
            depth += 1
        elif quote is None and char == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i + 1
        i += 1
    return None


def _extract_substitutions(text: str) -> tuple[str, list[str], bool]:
    """Pull $(...), `...`, >(...), and <(...) out of the outer command.

    Substitutions inside single quotes are left alone. Unbalanced input
    returns ok=False.
    """
    out: list[str] = []
    inners: list[str] = []
    quote = None
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if quote is None and char == "\\" and i + 1 < n:
            out.append(char)
            out.append(text[i + 1])
            i += 2
            continue
        if char in {"'", '"'} and (quote is None or quote == char):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            out.append(char)
            i += 1
            continue
        if quote != "'":
            opener = None
            if text.startswith("$(", i) or text.startswith(">(", i) or text.startswith("<(", i):
                opener = i + 1
            if opener is not None:
                found = _read_paren(text, opener)
                if found is None:
                    return text, inners, False
                inner, after = found
                inners.append(inner)
                out.append('""')
                i = after
                continue
            if quote is None and char == "`":
                close = text.find("`", i + 1)
                if close < 0:
                    return text, inners, False
                inners.append(text[i + 1 : close])
                out.append('""')
                i = close + 1
                continue
        out.append(char)
        i += 1
    return "".join(out), inners, True


def _split_semicolons(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote = None
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if quote is None and char == "\\" and i + 1 < n:
            buf.append(char)
            buf.append(text[i + 1])
            i += 2
            continue
        if char in {"'", '"'} and (quote is None or quote == char):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            buf.append(char)
            i += 1
            continue
        if quote is None and char in {";", "\n"}:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def _attached_redirect(token: str) -> tuple[str, str] | None:
    """Return (kind, target) when the operator and target share one token.

    kind is "write", "read", or "dup". A bare operator (">", ">>") is not
    attached; the caller reads the next token as the target. Longer operators
    are tried first so ">>" is not read as ">" plus a target of ">".
    """
    if re.fullmatch(r"\d*>&?\d+", token):
        return ("dup", "")
    if re.fullmatch(r"\d*<", token) or token == "<":
        return ("read", "")
    if token.startswith("<<"):
        return ("read", "")
    index = 0
    while index < len(token) and token[index].isdigit():
        index += 1
    rest = token[index:]
    # Longer operators first. An exact operator (">>", ">") is bare: the
    # next token is the target, so do not treat the second ">" as a path.
    for op in ("&>>", "&>", ">&", ">>", ">|", "<>", ">"):
        if rest == op:
            return None
        if rest.startswith(op):
            target = rest[len(op) :]
            if op == ">&" and target.isdigit():
                return ("dup", "")
            return ("write", target)
    return None


def _parse_segment(segment: str) -> tuple[list[str], list[str]] | None:
    """Return (argv, write_redirects). None when quoting is broken."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None
    argv: list[str] = []
    writes: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _SPLIT_TOKENS:
            # Keep the operator so the segment can be split into commands.
            argv.append(token)
            i += 1
            continue
        attached = _attached_redirect(token)
        if attached is not None:
            kind, target = attached
            if kind == "write" and target:
                writes.append(target)
                i += 1
                continue
            if kind == "read" and target == "" and not token.startswith("<<"):
                i += 2 if i + 1 < len(tokens) else 1
                continue
            if token.startswith("<<"):
                i += 1
                continue
            i += 1
            continue
        if token in {">", ">>", ">|", "&>", "&>>", ">&", "<>"} or re.fullmatch(
            r"\d*(>>|>\||>)", token
        ):
            if i + 1 >= len(tokens):
                return None
            target = tokens[i + 1]
            if token in {">&"} or re.fullmatch(r"\d*>&", token):
                if target.isdigit():
                    i += 2
                    continue
            writes.append(target)
            i += 2
            continue
        if token in {"<", "<<<"} or re.fullmatch(r"\d*<", token):
            i += 2 if i + 1 < len(tokens) else 1
            continue
        argv.append(token)
        i += 1
    return argv, writes


def _split_token_chains(tokens: list[str]) -> list[list[str]]:
    groups: list[list[str]] = [[]]
    for token in tokens:
        if token in _SPLIT_TOKENS:
            groups.append([])
        else:
            groups[-1].append(token)
    return [group for group in groups if group]


def _positionals(argv: list[str]) -> list[str]:
    items: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            items.extend(argv[i + 1 :])
            break
        if arg in _FLAG_WITH_ARG or (
            arg.startswith("--") and "=" not in arg and arg in _FLAG_WITH_ARG
        ):
            i += 2
            continue
        if arg.startswith("-") and arg != "-":
            i += 1
            continue
        items.append(arg)
        i += 1
    return items


def _dest_path(argv: list[str]) -> str | None:
    """Destination for cp/mv/ln/install/rsync, including -t dest."""
    dest = None
    positionals: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in {"-t", "--target-directory", "--target"}:
            if i + 1 < len(argv):
                dest = argv[i + 1]
                i += 2
                continue
            return dest
        if arg.startswith("--target-directory=") or arg.startswith("--target="):
            dest = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--":
            positionals.extend(argv[i + 1 :])
            break
        if arg in _FLAG_WITH_ARG:
            i += 2
            continue
        if arg.startswith("-") and arg != "-":
            i += 1
            continue
        positionals.append(arg)
        i += 1
    if dest is None and positionals:
        dest = positionals[-1]
    return dest


def _shell_inline(argv: list[str]) -> str | None:
    """Script passed to bash -c / bash -lc, if any."""
    for index, arg in enumerate(argv[1:], start=1):
        if arg in {"-c", "--command"} or (
            arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]
        ):
            if index + 1 < len(argv):
                return argv[index + 1]
            return ""
    return None


def _inline_blobs(argv: list[str]) -> list[str]:
    blobs: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in {"-c", "-e", "-E", "-r", "--eval"}:
            if i + 1 < len(argv):
                blobs.append(argv[i + 1])
            i += 2
            continue
        i += 1
    return blobs


def _is_inplace(cmd: str, argv: list[str]) -> bool:
    if cmd not in _INPLACE_CMDS:
        return False
    for arg in argv[1:]:
        if arg in {"-i", "--in-place"} or arg.startswith("--in-place="):
            return True
        if arg.startswith("-i") and cmd in {"sed", "perl", "ruby"}:
            return True
        if cmd == "perl" and arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]:
            return True
        if cmd == "gawk" and arg == "inplace":
            return True
    return False


def _git_subcommand(argv: list[str]) -> str | None:
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in {"-C", "-c", "--git-dir", "--work-tree"}:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg
    return None


def _unwrap(argv: list[str]) -> list[str]:
    if not argv or _base_cmd(argv[0]) not in _WRAPPERS:
        return argv
    cmd = _base_cmd(argv[0])
    i = 1
    while i < len(argv):
        arg = argv[i]
        if cmd in {"env", "sudo"} and "=" in arg and not arg.startswith("-"):
            i += 1
            continue
        if arg in {"-u", "-g", "--user", "--group", "-p", "--prompt"} or (
            cmd == "stdbuf" and arg in {"-i", "-o", "-e"}
        ):
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        break
    return argv[i:]


def _decide_argv(argv: list[str], depth: int) -> dict:
    if not argv:
        return _allow()
    if depth > 6:
        return _deny(None, "The command was nested too deeply to judge.")
    unwrapped = _unwrap(argv)
    if unwrapped is not argv and unwrapped != argv[: len(unwrapped)]:
        if not unwrapped:
            return _allow()
        return _decide_argv(unwrapped, depth + 1)
    cmd = _base_cmd(argv[0])
    if cmd in _SHELLS:
        inline = _shell_inline(argv)
        if inline is not None:
            if inline == "":
                return _deny(None, "A shell -c flag had no script, so the write was denied.")
            return decide_shell(inline, depth + 1)
    if cmd == "eval" and len(argv) > 1:
        return decide_shell(" ".join(argv[1:]), depth + 1)
    if cmd in _INTERPRETERS:
        for blob in _inline_blobs(argv):
            if mentions_secret(blob):
                return _deny(
                    None,
                    "Inline code names a secret env file and the hook cannot prove it only reads.",
                )
        for arg in argv[1:]:
            if not arg.startswith("-") and mentions_secret(arg) and is_secret_env_path(arg):
                return _deny(arg, "The command names that path and is not a plain read.")
    if _is_inplace(cmd, argv):
        for arg in _positionals(argv):
            if is_secret_env_path(arg):
                return _deny(arg, "In-place edit of that path is denied.")
        for arg in argv[1:]:
            if mentions_secret(arg) and not arg.startswith("-"):
                if is_secret_env_path(arg) or _SECRET_MENTION.fullmatch(arg.strip()):
                    return _deny(arg, "In-place edit of that path is denied.")
        # sed -i with a secret path buried in the expression still names the file
        # as a positional above. No positional secret → allow the in-place edit
        # of some other file.
        return _allow()
    if cmd == "dd":
        for arg in argv[1:]:
            if arg.startswith("of=") and is_secret_env_path(arg[3:]):
                return _deny(arg[3:], "dd of= targets that path.")
            if is_secret_env_path(arg):
                return _deny(arg, "dd names that path and the hook cannot prove it is only an input.")
        return _allow()
    for index, arg in enumerate(argv):
        if arg in {"-o", "--output", "--output-document", "--out"} and index + 1 < len(argv):
            target = argv[index + 1]
            if is_secret_env_path(target):
                return _deny(target, "An output flag targets that path.")
        for prefix in ("--output=", "--output-document=", "--out=", "-o"):
            if arg.startswith(prefix) and arg != "-o" and is_secret_env_path(arg[len(prefix) :]):
                return _deny(arg[len(prefix) :], "An output flag targets that path.")
    if cmd in _DEST_LAST:
        dest = _dest_path(argv)
        if dest and is_secret_env_path(dest):
            return _deny(dest, "The command's destination is that path.")
        return _allow()
    if cmd in _WRITE_ANY:
        for arg in _positionals(argv):
            if is_secret_env_path(arg):
                return _deny(arg, "The command writes that path.")
        return _allow()
    secret_args = [arg for arg in _positionals(argv) if is_secret_env_path(arg)]
    if not secret_args:
        for arg in _positionals(argv):
            if mentions_secret(arg) and _SECRET_MENTION.fullmatch(arg.strip()):
                secret_args.append(arg)
    if not secret_args:
        return _allow()
    path = secret_args[0]
    if cmd in _READERS or cmd in _NON_FILE:
        return _allow()
    if cmd == "git":
        sub = _git_subcommand(argv)
        if sub in _GIT_READ:
            return _allow()
        return _deny(path, "git would touch that path, and this subcommand is not a read.")
    return _deny(path, "The command names that path and the hook cannot prove it only reads.")


def _extract_heredocs(text: str) -> tuple[str, list[str]]:
    """Remove heredoc bodies so newlines inside them are not extra commands.

    The operator and delimiter stay in the command. Bodies are returned for a
    separate check: shell bodies are parsed as shell, interpreter bodies that
    name a secret env file are denied, and other bodies (commit messages, cat)
    are data.
    """
    bodies: list[str] = []
    out: list[str] = []
    quote = None
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if quote is None and char == "\\" and i + 1 < n:
            out.append(char)
            out.append(text[i + 1])
            i += 2
            continue
        if char in {"'", '"'} and (quote is None or quote == char):
            quote = None if quote == char else char
            out.append(char)
            i += 1
            continue
        if quote is None and text.startswith("<<", i) and not text.startswith("<<<", i):
            j = i + 2
            strip_tabs = False
            if j < n and text[j] == "-":
                strip_tabs = True
                j += 1
            delim_quote = None
            if j < n and text[j] in {"'", '"'}:
                delim_quote = text[j]
                j += 1
            start = j
            while j < n and not text[j].isspace() and text[j] not in {"'", '"'}:
                j += 1
            delim = text[start:j]
            if delim_quote and j < n and text[j] == delim_quote:
                j += 1
            out.append(text[i:j])
            i = j
            if not delim:
                continue
            newline = text.find("\n", i)
            if newline < 0:
                continue
            out.append(text[i : newline + 1])
            i = newline + 1
            body_lines: list[str] = []
            while i < n:
                newline = text.find("\n", i)
                line = text[i:newline] if newline >= 0 else text[i:]
                compare = line.lstrip("\t") if strip_tabs else line
                if compare == delim:
                    i = (newline + 1) if newline >= 0 else n
                    break
                body_lines.append(line)
                if newline < 0:
                    i = n
                    break
                i = newline + 1
            bodies.append("\n".join(body_lines))
            continue
        out.append(char)
        i += 1
    return "".join(out), bodies


def _command_names(masked: str) -> list[str]:
    names: list[str] = []
    for segment in _split_semicolons(masked):
        parsed = _parse_segment(segment)
        if parsed is None:
            continue
        argv, _writes = parsed
        for chain in _split_token_chains(argv):
            if chain:
                names.append(_base_cmd(chain[0]))
    return names


def decide_shell(command: str | None, depth: int = 0) -> dict:
    if not isinstance(command, str) or not command.strip():
        return _deny(None, "The shell command was missing, so the write was denied.")
    if depth > 6:
        return _deny(None, "The command was nested too deeply to judge.")
    stripped, inners, ok = _extract_substitutions(command)
    if not ok:
        if mentions_secret(command):
            return _deny(None, "A substitution could not be parsed and the command names a secret env file.")
        stripped = command
        inners = []
    for inner in inners:
        decision = decide_shell(inner, depth + 1)
        if decision["permission"] != "allow":
            return decision
    masked, heredocs = _extract_heredocs(stripped)
    for segment in _split_semicolons(masked):
        parsed = _parse_segment(segment)
        if parsed is None:
            if mentions_secret(segment):
                return _deny(None, "The command could not be parsed and it names a secret env file.")
            continue
        argv, writes = parsed
        for target in writes:
            if is_secret_env_path(target):
                return _deny(target, "A redirection writes that path.")
        chains = _split_token_chains(argv)
        if not chains:
            continue
        for chain in chains:
            decision = _decide_argv(chain, depth)
            if decision["permission"] != "allow":
                return decision
    if heredocs:
        names = _command_names(masked)
        shell_reads = any(name in _SHELLS for name in names)
        interpreter_reads = any(name in _INTERPRETERS for name in names)
        for body in heredocs:
            if shell_reads:
                decision = decide_shell(body, depth + 1)
                if decision["permission"] != "allow":
                    return decision
            elif interpreter_reads and mentions_secret(body):
                return _deny(
                    None,
                    "A heredoc passed to an interpreter names a secret env file.",
                )
    return _allow()


def _looks_like_patch(text: str) -> bool:
    return (
        "*** " in text
        or "diff --git" in text
        or text.startswith("+++")
        or "\n+++" in text
        or text.startswith("--- ")
        or "\n--- " in text
    )


def _paths_from_patch(text: str) -> list[str] | None:
    found: list[str] = []
    saw_header = False
    for line in text.splitlines():
        header = re.match(r"\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$", line)
        if header:
            saw_header = True
            found.append(header.group(1).strip())
            continue
        move = re.match(r"\*\*\*\s+Move to:\s*(.+?)\s*$", line)
        if move:
            saw_header = True
            found.append(move.group(1).strip())
            continue
        diff_git = re.match(r"diff --git \S+ (\S+)", line)
        if diff_git:
            saw_header = True
            path = diff_git.group(1)
            if path.startswith("b/"):
                path = path[2:]
            found.append(path)
            continue
        plus = re.match(r"(?:---|\+\+\+)\s+(.+?)\s*$", line)
        if plus:
            path = plus.group(1).strip()
            if path.startswith(("a/", "b/")) and not path[2:3].isspace():
                path = path[2:]
            saw_header = True
            if path not in {"/dev/null", "dev/null"}:
                found.append(path)
    if not saw_header:
        return None
    return found


def _collect_paths(node: object, paths: list[str], patches: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            norm = _norm_key(str(key))
            if norm in _SKIP_KEYS:
                continue
            if norm in _PATCH_KEYS and isinstance(value, str):
                # Only real patches count. An unrelated "input" string must
                # not fail closed on an ordinary edit.
                if _looks_like_patch(value):
                    patches.append(value)
                continue
            if norm in _PATH_KEYS:
                if isinstance(value, str) and "\n" not in value and len(value) < 1024:
                    paths.append(value)
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and "\n" not in item and len(item) < 1024:
                            paths.append(item)
                        else:
                            _collect_paths(item, paths, patches)
                    continue
            _collect_paths(value, paths, patches)
    elif isinstance(node, list):
        for item in node:
            _collect_paths(item, paths, patches)


def _decide_write(tool_input: object) -> dict:
    tool_input = _coerce(tool_input)
    paths: list[str] = []
    patches: list[str] = []
    if isinstance(tool_input, str):
        if _looks_like_patch(tool_input):
            patches.append(tool_input)
        elif "\n" not in tool_input and len(tool_input) < 1024:
            paths.append(tool_input)
        else:
            return _deny(None, "The write payload had no recognizable file path.")
    elif tool_input is not None:
        _collect_paths(tool_input, paths, patches)
    else:
        return _deny(None, "The write payload was empty.")
    for patch in patches:
        header_paths = _paths_from_patch(patch)
        if header_paths is None:
            return _deny(None, "A patch payload had no file headers, so it was denied.")
        paths.extend(header_paths)
    if not paths:
        return _deny(None, "The write payload had no recognizable file path.")
    for path in paths:
        if is_secret_env_path(path):
            return _deny(path, "Creating or editing that file is denied.")
    return _allow()


def _coerce(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


_READ_TOOLS = {"Read", "Grep", "Glob", "Delete", "Task", "WebSearch", "WebFetch"}
_WRITE_TOOLS = {
    "Write",
    "StrReplace",
    "ApplyPatch",
    "EditNotebook",
    "Edit",
    "search_replace",
    "MultiEdit",
    "NotebookEdit",
}


def evaluate(payload: dict) -> dict:
    event = str(payload.get("hook_event_name") or "")
    if event == "beforeShellExecution":
        command = payload.get("command")
        return decide_shell(command if isinstance(command, str) else None)
    tool = str(payload.get("tool_name") or "")
    if tool.startswith("MCP:"):
        return _allow()
    if tool in _READ_TOOLS:
        return _allow()
    if tool == "Shell" or (not tool and isinstance(payload.get("command"), str)):
        if tool == "Shell":
            tool_input = _coerce(payload.get("tool_input"))
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if command is None and isinstance(payload.get("command"), str):
                command = payload.get("command")
        else:
            command = payload.get("command")
        return decide_shell(command if isinstance(command, str) else None)
    if tool in _WRITE_TOOLS or not tool:
        # A payload with only a file path is treated as a write. An empty
        # object, or a write tool with no path, fails closed.
        if tool or "tool_input" in payload or "path" in payload or "file_path" in payload:
            tool_input = payload.get("tool_input", payload)
            return _decide_write(tool_input)
    return _deny(None, "The hook could not classify this call, so it was denied.")


def evaluate_raw(data: bytes) -> dict:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        return _deny(None, "Hook input was not valid UTF-8.")
    if not text.strip():
        return _deny(None, "Hook input was empty.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _deny(None, "Hook input was not valid JSON.")
    if not isinstance(payload, dict):
        return _deny(None, "Hook input was not a JSON object.")
    try:
        decision = evaluate(payload)
    except Exception:
        return _deny(None, "The hook failed while deciding, so the write was denied.")
    if decision.get("permission") not in {"allow", "deny"}:
        return _deny(None, "The hook produced an invalid decision, so the write was denied.")
    return decision


def _cases() -> list[tuple[dict, str]]:
    def write(path: str, **extra: object) -> dict:
        body = {"path": path, "contents": "KEY=value\n"}
        body.update(extra)
        return {"hook_event_name": "preToolUse", "tool_name": "Write", "tool_input": body}

    def shell(command: str) -> dict:
        return {
            "hook_event_name": "beforeShellExecution",
            "command": command,
            "cwd": "/tmp",
            "sandbox": False,
        }

    patch_readme = (
        "*** Begin Patch\n"
        "*** Update File: README.md\n"
        "@@\n"
        "-old\n"
        "+mentions .env in prose\n"
        "*** End Patch\n"
    )
    patch_env = "*** Begin Patch\n*** Update File: .env\n@@\n+X=1\n*** End Patch\n"
    return [
        (write(".env"), "deny"),
        (write(".env.local"), "deny"),
        (write(".env.example"), "deny"),
        (write("config/.env.production"), "deny"),
        (write("secrets.env"), "deny"),
        (write("prod.env.local"), "deny"),
        (write(".envrc"), "deny"),
        (write("/agent/repos/Pokemon-Box-EV/.env"), "deny"),
        (write("README.md", contents="do not commit .env\n"), "allow"),
        (
            {
                "tool_name": "Write",
                "tool_input": {
                    "path": "README.md",
                    "contents": "ok",
                    "input": "not a patch, mentions .env in prose",
                },
            },
            "allow",
        ),
        (write("environment.json"), "allow"),
        (write("foo.environment"), "allow"),
        (write("src/env.ts"), "allow"),
        (write("docs/runbooks/market-and-snapshots.md"), "allow"),
        (
            {
                "tool_name": "StrReplace",
                "tool_input": {
                    "path": "calc.html",
                    "old_string": ".env",
                    "new_string": ".env",
                },
            },
            "allow",
        ),
        (
            {
                "tool_name": "StrReplace",
                "tool_input": {"file_path": ".env", "old_string": "a", "new_string": "b"},
            },
            "deny",
        ),
        ({"tool_name": "Write", "tool_input": {"contents": "no path"}}, "deny"),
        ({"tool_name": "ApplyPatch", "tool_input": {"patch": patch_readme}}, "allow"),
        ({"tool_name": "ApplyPatch", "tool_input": {"patch": patch_env}}, "deny"),
        (
            {
                "tool_name": "ApplyPatch",
                "tool_input": {"patch": "diff --git a/README.md b/README.md\n+++ b/README.md\n"},
            },
            "allow",
        ),
        (
            {
                "tool_name": "EditNotebook",
                "tool_input": {"target_notebook": "notes.ipynb", "new_string": "x"},
            },
            "allow",
        ),
        ({"tool_name": "Read", "tool_input": {"path": ".env"}}, "allow"),
        ({"tool_name": "Shell", "tool_input": {"command": "echo hi > .env"}}, "deny"),
        ({"tool_name": "Shell", "tool_input": {"command": "git status"}}, "allow"),
        (shell("echo SECRET=1 > .env"), "deny"),
        (shell("echo SECRET=1 >> .env.local"), "deny"),
        (shell('echo SECRET=1 > ".env"'), "deny"),
        (shell("echo hi >.env"), "deny"),
        (shell("echo hi 2>.env"), "deny"),
        (shell("echo hi &>.env"), "deny"),
        (shell("echo hi 2>&1"), "allow"),
        (shell("cat .env | head"), "allow"),
        (shell("echo hi > README.md"), "allow"),
        (shell('echo "see .env" > README.md'), "allow"),
        (shell("cat .env"), "allow"),
        (shell("rg SECRET .env.local"), "allow"),
        (shell("sed -n '1p' .env"), "allow"),
        (shell("sed -i 's/a/b/' .env"), "deny"),
        (shell("sed -i.bak 's/a/b/' .env"), "deny"),
        (shell("perl -pi -e 's/a/b/' .env"), "deny"),
        (shell("tee .env"), "deny"),
        (shell("tee -a .env.local"), "deny"),
        (shell("cp foo .env"), "deny"),
        (shell("cp .env /tmp/out.txt"), "allow"),
        (shell("mv foo.txt .env.production"), "deny"),
        (shell("touch .env"), "deny"),
        (shell("touch README.md"), "allow"),
        (shell("dd of=.env"), "deny"),
        (shell("dd if=.env of=/tmp/out.txt"), "allow"),
        (shell(": > .env"), "deny"),
        (shell("python3 -c 'print(1)'"), "allow"),
        (shell("""python3 -c 'open(".env","w").write("x")'"""), "deny"),
        (shell("""python3 -c 'print(open(".env").read())'"""), "deny"),
        (shell("bash -c 'echo x > .env'"), "deny"),
        (shell("bash -lc 'cat .env'"), "allow"),
        (shell("git status"), "allow"),
        (shell("git diff -- .env"), "allow"),
        (shell("git checkout -- .env"), "deny"),
        (shell("git commit -m 'do not write .env'"), "allow"),
        (
            shell("git commit -m \"$(cat <<'EOF'\nmention .env in the message\nEOF\n)\""),
            "allow",
        ),
        (shell("python3 - <<'PY'\nopen('.env','w').write('x')\nPY"), "deny"),
        (shell("bash <<'EOF'\necho hi > .env\nEOF"), "deny"),
        (shell("bash <<'EOF'\necho hello\nEOF"), "allow"),
        (shell("printf '%s' '{\"path\":\".env\"}'"), "allow"),
        (shell("python3 tools/refresh_sealed_boxes.py --self-test"), "allow"),
        (shell("python3 .cursor/hooks/block-secret-env-writes.py --self-test"), "allow"),
        (shell("echo hi | tee .env"), "deny"),
        (shell("echo hi | tee notes.txt"), "allow"),
        (shell("curl -o .env https://example.com/x"), "deny"),
        (shell("source .env"), "allow"),
        (shell("vim .env"), "deny"),
        (shell("install -m 600 /tmp/x .env"), "deny"),
        (shell("ln -s foo .env"), "deny"),
        (shell("head -n 5 AGENTS.md"), "allow"),
        (shell("git add AGENTS.md docs/runbooks/market-and-snapshots.md && git status"), "allow"),
    ]


def run_self_test() -> int:
    failed = 0
    for payload, expected in _cases():
        decision = evaluate(payload)
        got = decision.get("permission")
        if got != expected:
            failed += 1
            sys.stderr.write(f"FAIL expected {expected} got {got}: {json.dumps(payload)[:240]}\n")
    raw_cases = [
        (b"", "deny"),
        (b"not-json", "deny"),
        (b"[]", "deny"),
        (b'{"tool_name":"Write","tool_input":{"path":"README.md","contents":"ok"}}', "allow"),
    ]
    for raw, expected in raw_cases:
        got = evaluate_raw(raw).get("permission")
        if got != expected:
            failed += 1
            sys.stderr.write(f"FAIL raw expected {expected} got {got}: {raw[:80]!r}\n")
    # Allow JSON must not carry a deny message. Deny JSON must name permission.
    allow = evaluate_raw(b'{"command":"git status","hook_event_name":"beforeShellExecution"}')
    deny = evaluate_raw(b'{"command":"echo x > .env","hook_event_name":"beforeShellExecution"}')
    if set(allow) != {"permission"} or allow["permission"] != "allow":
        failed += 1
        sys.stderr.write(f"FAIL allow shape {allow}\n")
    if deny.get("permission") != "deny" or "agent_message" not in deny:
        failed += 1
        sys.stderr.write(f"FAIL deny shape {deny}\n")
    total = len(_cases()) + len(raw_cases) + 2
    if failed:
        sys.stderr.write(f"{failed} failed out of {total}\n")
        return 1
    sys.stdout.write(f"ok {total} secret-env hook cases\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed secret env write hook.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in allow/deny cases and do not read hook stdin.",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        decision = evaluate_raw(sys.stdin.buffer.read())
    except Exception:
        decision = _deny(None, "The hook crashed, so the write was denied.")
    sys.stdout.write(json.dumps(decision, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
