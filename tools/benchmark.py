#!/usr/bin/env python3
"""Compare the speed of fregex with Python's `re`.

Writes the benchmark cases (patterns and generated subject texts) to a work directory, times
Python's `re` on byte strings, builds and runs bench/benchmark.f90 on the same cases, checks
that both give the same results, and prints a Markdown table. With --readme, the table replaces
the text between the benchmark markers in README.md.

Both sides use the same protocol as `timeit`: the number of loops grows (1, 2, 5, 10, ...) until
one run takes at least --min-time seconds, and the best of --repeats runs is reported. Patterns
are compiled outside the timed loop, except in the compile cases.

Usage: benchmark.py [--readme] [--min-time S] [--repeats N] [--flags FFLAGS] [--work DIR]

Edit CASES below to add or change benchmarks.
"""

import argparse
import datetime
import platform
import random
import re
import subprocess
import sys
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
START_MARK = "<!-- benchmark:start -->"
END_MARK = "<!-- benchmark:end -->"

# Compiler flags added to fpm's release profile (which already has -O3 -funroll-loops).
DEFAULT_FFLAGS = "-O3 -march=native -flto"

# Speed ratios within this fraction of 1 get a yellow box in the table instead of green or red.
CLOSE = 0.1

FLAG_MAP = {1: re.IGNORECASE, 2: re.MULTILINE, 4: re.DOTALL}
I, M = 1, 2

VOCAB = (
    "the of and to in is was for on that with as by at from it be this an are or which have "
    "alpha beta gamma delta value array index module program function result string number "
    "matrix vector solver integer real logical character type data"
).split()


# --------------------------------------------------------------------------------------------
# Subject texts (deterministic)


def prose(rng, nbytes=200_000):
    """Lines of words, with some capitalized and doubled words, numbers and e-mail addresses."""
    lines, size = [], 0
    while size < nbytes:
        words = []
        for _ in range(rng.randint(6, 14)):
            r = rng.random()
            if r < 0.02:
                words.append(f"{rng.choice(VOCAB)}.{rng.choice(VOCAB)}@{rng.choice(VOCAB)}.org")
            elif r < 0.05:
                words.append(str(rng.randint(0, 99999)))
            elif r < 0.07 and words:
                words.append(words[-1])
            else:
                w = rng.choice(VOCAB)
                words.append(w.capitalize() if rng.random() < 0.1 else w)
        line = " ".join(words) + rng.choice([".", ",", ";", ""])
        lines.append(line)
        size += len(line) + 1
    return ("\n".join(lines) + "\n").encode()


def log(rng, nlines=4000):
    levels = ["INFO"] * 8 + ["DEBUG"] * 4 + ["WARNING", "Error", "ERROR"]
    out = []
    for _ in range(nlines):
        stamp = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d} " \
                f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        msg = " ".join(rng.choice(VOCAB) for _ in range(rng.randint(3, 10)))
        out.append(f"{stamp} {rng.choice(levels)} {rng.choice(VOCAB)}: {msg}")
    return ("\n".join(out) + "\n").encode()


def fortran(rng, nlines=3000):
    types = ["integer", "real(wp)", "logical", "character(len=*)", "type(regex_t)"]
    attrs = ["", ", intent(in)", ", intent(out)", ", allocatable", ", dimension(:)"]
    out = []
    for _ in range(nlines):
        r = rng.random()
        indent = " " * rng.choice([4, 8, 12])
        names = ", ".join(f"{rng.choice(VOCAB)}{rng.randint(1, 9)}" for _ in range(rng.randint(1, 4)))
        if r < 0.3:
            out.append(f"{indent}{rng.choice(types)}{rng.choice(attrs)} :: {names}")
        elif r < 0.5:
            out.append(f"{indent}call {rng.choice(VOCAB)}_{rng.choice(VOCAB)}({names})")
        elif r < 0.6:
            out.append(f"{indent}! {' '.join(rng.choice(VOCAB) for _ in range(6))}")
        else:
            out.append(f"{indent}{rng.choice(VOCAB)}{rng.randint(1, 9)} = {names.replace(', ', ' + ')}")
    return ("\n".join(out) + "\n").encode()


def csv_line(rng, nfields=2000):
    seps = [",", ", ", " ,", " , "]
    return "".join(rng.choice(VOCAB) + rng.choice(seps) for _ in range(nfields)).encode() + b"end"


def make_subjects():
    rng = random.Random(20261024)
    text = prose(rng)
    return {
        "prose": text,
        "prose+needle": text + b"needle\n",
        "log": log(rng),
        "fortran": fortran(rng),
        "csv": csv_line(rng),
        "date": b"2026-10-24",
        "": b"",
    }


# --------------------------------------------------------------------------------------------
# Cases: (name, op, flags, pattern, subject key, replacement)

CASES = [
    ("literal, match at end", "search", 0, rb"needle", "prose+needle", b""),
    ("no match (full scan)", "search", 0, rb"[XYZ]{2}\d", "prose", b""),
    ("words", "find_all", 0, rb"\w+", "prose", b""),
    ("case-insensitive literal", "find_all", I, rb"error", "log", b""),
    ("dates", "find_all", 0, rb"\d{4}-\d{2}-\d{2}", "log", b""),
    ("e-mail addresses", "find_all", 0, rb"[\w.+-]+@[\w-]+\.\w+", "prose", b""),
    ("alternation of words", "find_all", 0, rb"\b(?:alpha|beta|gamma|delta)\b", "prose", b""),
    ("Fortran declarations", "find_all", I | M,
     rb"^\s*(?P<type>integer|real|logical|character|type)\b.*::\s*(?P<vars>.*)$", "fortran", b""),
    ("lookbehind", "find_all", 0, rb"(?<=call )\w+", "fortran", b""),
    ("backreference (doubled words)", "find_all", 0, rb"\b(\w+)\s+\1\b", "prose", b""),
    ("collapse whitespace", "sub", 0, rb"\s+", "prose", b" "),
    ("swap with groups", "sub", 0, rb"(\w+)@(\w+)", "prose", rb"\2 at \1"),
    ("split CSV", "split", 0, rb"\s*,\s*", "csv", b""),
    ("short fullmatch", "fullmatch", 0, rb"(\d{4})-(\d{2})-(\d{2})", "date", b""),
    ("compile e-mail pattern", "compile", 0, rb"[\w.+-]+@[\w-]+\.\w+", "", b""),
    ("compile declaration pattern", "compile", I | M,
     rb"^\s*(?P<type>integer|real|logical|character|type)\b.*::\s*(?P<vars>.*)$", "", b""),
]


# --------------------------------------------------------------------------------------------
# Python side


def py_flags(flags):
    out = 0
    for bit, flag in FLAG_MAP.items():
        if flags & bit:
            out |= flag
    return out


def python_case(op, flags, pattern, subject, repl):
    """Returns (statement, namespace, check function) for timeit."""
    fl = py_flags(flags)
    r = re.compile(pattern, fl)
    ns = {"r": r, "s": subject, "repl": repl, "p": pattern, "fl": fl, "compile": re._compiler.compile}
    if op == "compile":   # bypass re's cache of compiled patterns
        return "compile(p, fl)", ns, lambda: r.groups
    if op == "search":
        return "r.search(s)", ns, lambda: (m := r.search(subject)) and m.start() + 1 or 0
    if op == "fullmatch":
        return "r.fullmatch(s)", ns, lambda: int(r.fullmatch(subject) is not None)
    if op == "find_all":   # finditer, since fregex returns match objects rather than strings
        return "list(r.finditer(s))", ns, lambda: len(list(r.finditer(subject)))
    if op == "sub":
        return "r.sub(repl, s)", ns, lambda: len(r.sub(repl, subject))
    if op == "split":
        return "r.split(s)", ns, lambda: len(r.split(subject))
    raise ValueError(op)


def time_python(stmt, ns, min_time, repeats):
    timer = timeit.Timer(stmt, globals=ns)
    n = 1
    k = 0
    while True:   # same loop counts as bench/benchmark.f90
        t = timer.timeit(n)
        if t >= min_time:
            break
        k += 1
        n = (1, 2, 5)[k % 3] * 10 ** (k // 3)
    return min([t] + timer.repeat(repeats - 1, n)) / n


# --------------------------------------------------------------------------------------------
# Fortran side


def write_cases(work, subjects):
    work.mkdir(parents=True, exist_ok=True)
    for key, data in subjects.items():
        (work / f"subject_{key.replace('+', '_') or 'empty'}.txt").write_bytes(data)
    lines = []
    for i, (name, op, flags, pattern, key, repl) in enumerate(CASES):
        (work / f"{i:02d}.pat").write_bytes(pattern)
        (work / f"{i:02d}.repl").write_bytes(repl)
        subj = f"subject_{key.replace('+', '_') or 'empty'}.txt"
        lines.append(f"case{i:02d} {op} {flags} {i:02d}.pat {subj} {i:02d}.repl")
    (work / "cases.txt").write_text("\n".join(lines) + "\n")


def run_fortran(work, fflags, min_time, repeats):
    fpm = ["fpm", "run", "--example", "benchmark", "--profile", "release", "--flag", fflags]
    subprocess.run(fpm[:1] + ["build"] + fpm[3:], cwd=ROOT, check=True, capture_output=True)
    out = subprocess.run(fpm + ["--", str(work), str(min_time), str(repeats)],
                         cwd=ROOT, check=True, capture_output=True, text=True).stdout
    results = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].startswith("case"):
            results[int(parts[0][4:])] = (float(parts[1]), int(parts[2]))
    return results


def compiler_version():
    try:
        out = subprocess.run(["gfortran", "--version"], capture_output=True, text=True).stdout
        return re.sub(r"\s*\(.*?\)\s*", " ", out.splitlines()[0]).strip()
    except (OSError, IndexError):
        return "gfortran"


def cpu_name():
    try:
        if sys.platform == "darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                  capture_output=True, text=True).stdout.strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


# --------------------------------------------------------------------------------------------
# Output


def fmt_time(t):
    for unit, scale in (("s", 1), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)):
        if t >= scale:
            return f"{t / scale:.3g} {unit}"
    return f"{t / 1e-9:.3g} ns"


def fmt_size(n):
    return f"{n / 1000:.0f} KB" if n >= 1000 else f"{n} B"


def table(rows, subjects, fflags):
    out = [
        "| Case | Operation | Pattern | Subject | Python `re` | fregex | fregex speed |",
        "|---|---|---|---|--:|--:|--:|",
    ]
    for (name, op, flags, pattern, key, repl), tp, tf in rows:
        pat = pattern.decode()
        if flags:
            pat = "(?" + ("i" if flags & I else "") + ("m" if flags & M else "") + ")" + pat
        pat = "`" + pat.replace("|", "\\|") + "`"
        subj = "—" if not key else f"{key.split('+')[0]}, {fmt_size(len(subjects[key]))}"
        ratio = tp / tf
        box = "🟨" if abs(ratio - 1) < CLOSE else "🟩" if ratio > 1 else "🟥"
        speed = f"{box} {ratio:.2f}× faster" if ratio >= 1 else f"{box} {1 / ratio:.2f}× slower"
        out.append(f"| {name} | `{op}` | {pat} | {subj} | {fmt_time(tp)} | {fmt_time(tf)} | {speed} |")
    out.append("")
    out.append(f"Measured {datetime.date.today().isoformat()} on {cpu_name()}: Python "
               f"{platform.python_version()} `re` on `bytes`, {compiler_version()} with "
               f"`--profile release --flag \"{fflags}\"`. Times are per operation, best of the "
               f"repeated runs. Regenerate with `python -I tools/benchmark.py --readme`.")
    return "\n".join(out)


def update_readme(text):
    readme = README.read_text()
    i, j = readme.find(START_MARK), readme.find(END_MARK)
    if i < 0 or j < i:
        sys.exit(f"benchmark markers not found in {README}")
    README.write_text(readme[:i + len(START_MARK)] + "\n\n" + text + "\n\n" + readme[j:])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--readme", action="store_true", help="write the table into README.md")
    ap.add_argument("--min-time", type=float, default=0.2, help="minimum seconds per timed run")
    ap.add_argument("--repeats", type=int, default=5, help="timed runs per case (best is kept)")
    ap.add_argument("--flags", default=DEFAULT_FFLAGS, help="extra Fortran compiler flags")
    ap.add_argument("--work", type=Path, default=ROOT / "build" / "bench", help="work directory")
    args = ap.parse_args()

    subjects = make_subjects()
    write_cases(args.work, subjects)
    print("running fregex ...", file=sys.stderr)
    fortran_results = run_fortran(args.work, args.flags, args.min_time, args.repeats)

    rows, ok = [], True
    for i, case in enumerate(CASES):
        name, op, flags, pattern, key, repl = case
        print(f"running Python: {name} ...", file=sys.stderr)
        stmt, ns, check = python_case(op, flags, pattern, subjects[key], repl)
        tp = time_python(stmt, ns, args.min_time, args.repeats)
        tf, fcheck = fortran_results[i]
        if fcheck != check():
            print(f"MISMATCH in {name!r}: Python {check()}, fregex {fcheck}", file=sys.stderr)
            ok = False
        rows.append((case, tp, tf))

    text = table(rows, subjects, args.flags)
    print(text)
    if not ok:
        sys.exit("results differ between Python and fregex; README not updated")
    if args.readme:
        update_readme(text)
        print(f"updated {README}", file=sys.stderr)


if __name__ == "__main__":
    main()
