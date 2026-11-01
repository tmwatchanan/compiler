#!/usr/bin/env python3
"""Differential fuzzer for the stack-size estimate (runs inside the container).

Generates random Pawn programs with call graphs that include cycles and
self-recursion, compiles each with the old and the new pawncc under several
flag sets, and compares stdout, stderr, exit code and the .amx bytes.

  python3 fuzz.py --old /work/bin/old-Debug --new /work/bin/new-Debug \
      --count 400 --seed 1 [--max-funcs 12] [--keep /work/fuzz-fail]
"""
import argparse
import os
import random
import shutil
import subprocess
import sys

WORKDIR = "/tmp/fuzz"
SRC = os.path.join(WORKDIR, "case.pwn")
AMX = os.path.join(WORKDIR, "case.amx")

FLAG_SETS = [
    ["-;+", "-(+", "-d3"],
    ["-;+", "-(+", "-d3", "-R+"],
    ["-;+", "-(+", "-v2", "-O0", "-R+"],
    ["-;+", "-(+", "-\\", "-Z-", "-d3", "-O0", "-t4"],
]


def gen_program(rng, max_funcs):
    """Return the text of a random program."""
    n = rng.randint(2, max_funcs)
    dense = rng.random() < 0.5
    cyclic = rng.random() < 0.3
    nparams = [rng.randint(0, 3) for _ in range(n)]
    kinds = [rng.choice(["plain", "plain", "public", "stock"]) for _ in range(n)]

    def call_expr(j):
        args = ", ".join(str(rng.randint(0, 9)) for _ in range(nparams[j]))
        return "f%d(%s)" % (j, args)

    bodies = {}
    for i in range(n):
        if dense:
            k = rng.randint(0, n)
        else:
            k = rng.randint(0, 2)
        callees = []
        for _ in range(k):
            if cyclic and rng.random() < 0.6:
                # back edge (to a lower index or self)
                j = rng.randint(0, i)
            else:
                j = rng.randrange(n)
            callees.append(j)
        lines = []
        params = ["a", "b", "c"][:nparams[i]]
        lines.append("    new v[%d];" % rng.randint(1, 40))
        lines.append("    v[0] = %s;" % (" + ".join(params) if params else "0"))
        for j in callees:
            if rng.random() < 0.5:
                lines.append("    %s;" % call_expr(j))
            else:
                lines.append("    v[0] += %s;" % call_expr(j))
        lines.append("    return v[0];")
        bodies[i] = lines

    out = []
    # Forward declarations so call order never matters.
    for i in range(n):
        plist = ", ".join(["a", "b", "c"][:nparams[i]])
        if kinds[i] == "stock":
            continue
        out.append("forward f%d(%s);" % (i, plist))
    out.append("")
    order = list(range(n))
    rng.shuffle(order)  # decides the order of each function's caller list
    for i in order:
        plist = ", ".join(["a", "b", "c"][:nparams[i]])
        prefix = {"plain": "", "public": "public ", "stock": "stock "}[kinds[i]]
        out.append("%sf%d(%s)" % (prefix, i, plist))
        out.append("{")
        out.extend(bodies[i])
        out.append("}")
        out.append("")
    out.append("main()")
    out.append("{")
    subset = rng.sample(range(n), rng.randint(1, n))
    for j in subset:
        out.append("    %s;" % call_expr(j))
    out.append("}")
    out.append("")
    return "\n".join(out)


def run_compiler(bindir, flags):
    """Compile SRC; return (rc, stdout, stderr, amx bytes or None) or None on timeout."""
    if os.path.exists(AMX):
        os.remove(AMX)
    env = dict(os.environ, LD_LIBRARY_PATH=bindir)
    try:
        p = subprocess.run([os.path.join(bindir, "pawncc"), "case.pwn"] + flags,
                           cwd=WORKDIR, env=env, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    amx = None
    if os.path.exists(AMX):
        with open(AMX, "rb") as f:
            amx = f.read()
        os.remove(AMX)
    return (p.returncode, p.stdout, p.stderr, amx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-funcs", type=int, default=12)
    ap.add_argument("--keep", default=None)
    args = ap.parse_args()

    os.makedirs(WORKDIR, exist_ok=True)
    cases = compiles = mismatches = skipped = recursion = estimate = compile_fail = 0

    for i in range(args.count):
        rng = random.Random("%d-%d" % (args.seed, i))
        prog = gen_program(rng, args.max_funcs)
        with open(SRC, "w") as f:
            f.write(prog)
        cases += 1
        case_rec = case_est = False
        case_bad = False
        results = []  # (flags, old, new) for the keep dump
        too_big = False
        for flags in FLAG_SETS:
            old = run_compiler(args.old, flags)
            if old is None:
                too_big = True
                break
            new = run_compiler(args.new, flags)
            compiles += 1
            out = old[1].decode("utf-8", "replace")
            if "recursion detected" in out:
                case_rec = True
            if "estimated max. usage" in out:
                case_est = True
            if old[3] is None and old[0] != 0:
                compile_fail += 1
            if new is None or old != new:
                case_bad = True
            results.append((flags, old, new))
        if too_big:
            skipped += 1
            continue
        recursion += case_rec
        estimate += case_est
        if case_bad:
            mismatches += 1
            print("MISMATCH case %d-%d" % (args.seed, i))
            if args.keep:
                d = os.path.join(args.keep, "%d-%d" % (args.seed, i))
                os.makedirs(d, exist_ok=True)
                shutil.copy(SRC, os.path.join(d, "case.pwn"))
                for k, (flags, old, new) in enumerate(results):
                    for tag, r in (("old", old), ("new", new)):
                        if r is None:
                            r = (-1, b"TIMEOUT", b"", None)
                        base = os.path.join(d, "flags%d.%s" % (k, tag))
                        with open(base + ".stdout", "wb") as f:
                            f.write(r[1])
                        with open(base + ".stderr", "wb") as f:
                            f.write(r[2])
                        with open(base + ".rc", "w") as f:
                            f.write("%s %s\n" % (r[0], " ".join(flags)))
                        if r[3] is not None:
                            with open(base + ".amx", "wb") as f:
                                f.write(r[3])

    print("cases=%d compiles=%d mismatches=%d skipped_old_timeout=%d "
          "recursion_cases=%d estimate_cases=%d failed_compiles=%d"
          % (cases, compiles, mismatches, skipped, recursion, estimate, compile_fail))
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
