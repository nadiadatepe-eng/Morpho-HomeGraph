#!/usr/bin/env python3
"""L5: the project's tree as a graph the view can draw.

The answer key is `tests/gold/FASIT-cp11.md`, written before this module.

**Folders and files, not L3's links.** What the user pointed at is a tree, and
that is what the first view draws. The link graph over the same picture is its
own checkpoint -- drawing both at once would make "the picture changed" an
answer with two possible causes.

**File-type buckets at high fanout, K = 4.** That is morpho's own fanout cap
for inserting BUF cells, borrowed with its source: a folder with more than four
direct files gets one intermediate node per file type, and the files hang off
*it* rather than off the folder. A folder at or under the cap gets none --
without that limit "bucket" is just an extra step in every path.

**A bucket moves an edge, it never adds one.** A file that hung off both the
folder and the bucket would be counted twice by everything downstream, and the
picture would look right while doing it.

**Names are exported untouched.** `<script>` is a legal filename, and this
layer does not sanitise it: the page sets text rather than markup, and that is
the protection (R6). Escaping here would put the burden on every consumer and
hide the bytes that are actually on the disk.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .store import PROJECT

# morpho's fanout cap for BUF insertion. Borrowed with its source rather than
# measured on filesystems by us -- an honest loan, and the answer key says so.
K = 4

# The bucket's id is the folder's, a separator, and the file type. **NUL,
# because it is the one byte a POSIX filename cannot contain** -- so no file
# can ever be named such that its id collides with a bucket's.
#
# This was `\x1f` (US) first, on the belief that a control character "cannot
# occur in a path component". That is false on Linux: only `/` and NUL are
# forbidden, so a file literally named `\x1fmd` in the root would have shared
# an id with the root's `md` bucket, and the tree would have grown a node with
# two roles. Only NUL makes the claim true.
SEP = "\0"

# What the view directory holds. Copied beside the data so the result is one
# self-contained folder: a page that reaches back into the repository works on
# the machine it was built on and nowhere else.
PAGE = ("index.html", "js", "graphs_engine")


class NothingToDraw(RuntimeError):
    """The project has no content yet, so a picture would be a lie."""


def kind_of(name: str) -> str:
    """The file type a bucket groups by. The suffix, or `(none)`."""
    suffix = Path(name).suffix.lower()
    return suffix[1:] if suffix else "(none)"


def graph(store, root: str) -> dict:
    """`{"root": str, "nodes": [...], "edges": [...]}` for one project.

    Paths are project-relative (R9). Sorted on the way out so two exports of
    one store are byte-identical -- the data half of "the same corpus draws
    the same picture twice".
    """
    if store.role != PROJECT:
        raise NothingToDraw("a view belongs to a project store, not a %r one"
                            % store.role)
    rows = [path for (path,) in store.db.execute(
        "SELECT path FROM content ORDER BY path")]
    if not rows:
        raise NothingToDraw("this project has no content yet")

    nodes: dict[str, dict] = {"": {"id": "", "kind": "dir", "name": ".",
                                   "path": "", "depth": 0}}
    parent_of: dict[str, str] = {}
    for absolute in rows:
        relative = os.path.relpath(absolute, root)
        parts = relative.split(os.sep)
        walked = ""
        for depth, part in enumerate(parts[:-1], start=1):
            child = "%s/%s" % (walked, part) if walked else part
            if child not in nodes:
                nodes[child] = {"id": child, "kind": "dir", "name": part,
                                "path": child, "depth": depth}
                parent_of[child] = walked
            walked = child
        leaf = relative.replace(os.sep, "/")
        nodes[leaf] = {"id": leaf, "kind": "file", "name": parts[-1],
                       "path": leaf, "depth": len(parts),
                       "type": kind_of(parts[-1])}
        parent_of[leaf] = walked

    _bucket(nodes, parent_of)
    edges = [{"from": parent, "to": child}
             for child, parent in sorted(parent_of.items())]
    return {"root": ".", "nodes": sorted(nodes.values(),
                                         key=lambda node: node["id"]),
            "edges": sorted(edges, key=lambda e: (e["from"], e["to"]))}


def _bucket(nodes: dict, parent_of: dict) -> None:
    """Insert one bucket per file type in folders above the fanout cap.

    In place, and it *moves* each file's parent rather than adding a second
    one (R5). The depth of a bucketed file goes up by one, because it is now
    one step further from the folder -- a depth that did not follow the move
    would put the file at the bucket's level in every drawing that reads it.
    """
    direct: dict[str, list[str]] = {}
    for child, parent in parent_of.items():
        if nodes[child]["kind"] == "file":
            direct.setdefault(parent, []).append(child)
    for folder, files in direct.items():
        if len(files) <= K:
            continue
        for file_id in files:
            bucket = "%s%s%s" % (folder, SEP, nodes[file_id]["type"])
            if bucket not in nodes:
                nodes[bucket] = {"id": bucket, "kind": "bucket",
                                 "name": nodes[file_id]["type"],
                                 "path": bucket,
                                 "depth": nodes[folder]["depth"] + 1}
                parent_of[bucket] = folder
            parent_of[file_id] = bucket
            nodes[file_id]["depth"] = nodes[bucket]["depth"] + 1


def write(store, root: str, out: Path, page: Path) -> dict:
    """Write a self-contained view directory. Returns the graph's tally.

    The page, the engine and the data land in one folder on purpose: a view
    that reaches back into the repository for its JavaScript works on the
    machine it was built on and nowhere else.
    """
    data = graph(store, root)
    # Writing the view *into* its own source would delete `js/` and then copy
    # from the hole it just made. Refused rather than guarded per file: there
    # is no useful meaning for `--out view`, and a half-deleted checkout is a
    # bad way to find that out.
    if out.resolve() == page.resolve() or page.resolve() in out.resolve().parents:
        raise NothingToDraw(
            "the output folder %s is inside the page's own source %s -- pick "
            "another --out" % (out, page))
    out.mkdir(parents=True, exist_ok=True)
    for name in PAGE:
        source = page / name
        target = out / name
        if source.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    with open(out / "data.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    kinds = {"dir": 0, "file": 0, "bucket": 0}
    for node in data["nodes"]:
        kinds[node["kind"]] += 1
    return {"nodes": len(data["nodes"]), "edges": len(data["edges"]), **kinds}
