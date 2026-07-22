#!/usr/bin/env python3
"""
okf_lint.py - Validatore di conformita' per bundle OKF v0.1.

Uso:
    python okf_lint.py <bundle_dir>
    python okf_lint.py <bundle_dir> --fix     # rigenera index.md e normalizza timestamp
    python okf_lint.py <bundle_dir> --json    # output machine-readable

Controlla:
  - presenza e validita' del frontmatter YAML
  - campo obbligatorio `type` (unico requisito hard della spec OKF)
  - campi convenzionali raccomandati: title, description, timestamp
  - timestamp in ISO 8601
  - link markdown interni rotti
  - pagine orfane (nessun link entrante)
  - index.md disallineati rispetto al contenuto della cartella
  - nomi riservati usati impropriamente
Exit code 1 se ci sono errori (non warning).
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RESERVED = {"index.md", "log.md"}
SKIP_DIRS = {"raw", ".git", ".obsidian", "node_modules", "assets"}
REQUIRED = ["type"]
RECOMMENDED = ["title", "description", "timestamp"]

LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def parse_frontmatter(text):
    """Parser YAML minimale: chiave: valore, liste inline [a, b]. Nessuna dipendenza."""
    m = FM_RE.match(text)
    if not m:
        return None, text
    fm, body = {}, text[m.end() :]
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            v = (
                [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
                if inner
                else []
            )
        else:
            v = v.strip("'\"")
        fm[k] = v
    return fm, body


def iso_ok(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def collect(root):
    out = []
    for p in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return out


def resolve(root, page, target):
    target = target.split("#")[0].strip()
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None
    if target.startswith("/"):
        return (root / target.lstrip("/")).resolve()
    return (page.parent / target).resolve()


def lint(root, fix=False):
    errors, warnings, fixes = [], [], []
    pages = collect(root)
    if not pages:
        errors.append(
            {"file": str(root), "msg": "nessun file markdown trovato nel bundle"}
        )
        return errors, warnings, fixes

    inbound = {p.resolve(): 0 for p in pages}
    types = {}

    for page in pages:
        rel = page.relative_to(root).as_posix()
        text = page.read_text(encoding="utf-8", errors="replace")
        fm, _ = parse_frontmatter(text)

        is_reserved = page.name in RESERVED

        if fm is None:
            if not is_reserved:
                errors.append({"file": rel, "msg": "frontmatter YAML assente"})
        else:
            for key in REQUIRED:
                if not fm.get(key) and not is_reserved:
                    errors.append(
                        {
                            "file": rel,
                            "msg": f"campo obbligatorio OKF mancante: `{key}`",
                        }
                    )
            for key in RECOMMENDED:
                if not fm.get(key) and not is_reserved:
                    warnings.append(
                        {"file": rel, "msg": f"campo raccomandato mancante: `{key}`"}
                    )
            ts = fm.get("timestamp")
            if ts and not iso_ok(ts):
                if fix:
                    new = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    page.write_text(
                        text.replace(f"timestamp: {ts}", f"timestamp: {new}", 1),
                        encoding="utf-8",
                    )
                    fixes.append(
                        {"file": rel, "msg": f"timestamp normalizzato -> {new}"}
                    )
                else:
                    errors.append({"file": rel, "msg": f"timestamp non ISO 8601: {ts}"})
            if fm.get("type"):
                types.setdefault(str(fm["type"]), []).append(rel)

        for label, target in LINK_RE.findall(text):
            dest = resolve(root, page, target)
            if dest is None:
                continue
            if not dest.exists():
                errors.append({"file": rel, "msg": f"link rotto: [{label}]({target})"})
            elif dest in inbound and dest != page.resolve():
                inbound[dest] += 1

    for page in pages:
        if page.name in RESERVED:
            continue
        if inbound.get(page.resolve(), 0) == 0:
            warnings.append(
                {
                    "file": page.relative_to(root).as_posix(),
                    "msg": "pagina orfana: nessun link entrante",
                }
            )

    # index.md per categoria: segnala file non elencati
    for d in sorted({p.parent for p in pages}):
        idx = d / "index.md"
        siblings = [p for p in pages if p.parent == d and p.name not in RESERVED]
        if not siblings:
            continue
        if not idx.exists():
            warnings.append(
                {
                    "file": (d / "index.md").relative_to(root).as_posix(),
                    "msg": f"index.md assente per {len(siblings)} pagine",
                }
            )
            continue
        itext = idx.read_text(encoding="utf-8", errors="replace")
        missing = [p.name for p in siblings if p.name not in itext]
        if missing:
            warnings.append(
                {
                    "file": idx.relative_to(root).as_posix(),
                    "msg": "pagine non indicizzate: "
                    + ", ".join(missing[:10])
                    + (f" (+{len(missing) - 10})" if len(missing) > 10 else ""),
                }
            )

    singles = [t for t, ps in types.items() if len(ps) == 1]
    if len(types) > 6 and singles:
        warnings.append(
            {
                "file": "<bundle>",
                "msg": f"{len(types)} type distinti, {len(singles)} usati una sola volta: "
                "valuta di consolidarli e allineare AGENTS.md",
            }
        )

    return errors, warnings, fixes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = Path(a.bundle).resolve()
    if not root.is_dir():
        print(f"errore: {root} non e' una directory", file=sys.stderr)
        return 2

    errors, warnings, fixes = lint(root, fix=a.fix)

    if a.json:
        print(
            json.dumps(
                {"errors": errors, "warnings": warnings, "fixes": fixes},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        for f in fixes:
            print(f"FIX   {f['file']}: {f['msg']}")
        for e in errors:
            print(f"ERROR {e['file']}: {e['msg']}")
        for w in warnings:
            print(f"WARN  {w['file']}: {w['msg']}")
        print(
            f"\n{len(errors)} errori, {len(warnings)} warning"
            + (f", {len(fixes)} correzioni" if fixes else "")
        )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
