# Stack-size estimate: exact memo instead of path enumeration

pawncc estimates the stack a script needs after it has written the `.amx` (`max_stacksize()` in
`source/compiler/sc1.c`). On a large script this estimate took about 98 % of the
compile time: 521 s for `main.pwn` alone, against 6-8 s with `-e<file>`, which skips it. This
change makes the estimate itself fast. For every input that the old code handled without
crashing, the output stays byte-identical: the `.amx` and every printed line.

## The original algorithm

- `sym->refer[]` lists the **callers** of a function (`refer_symbol()` in sc2.c). For every user
  function, `max_stacksize()` starts a depth-first walk upward through the callers.
- At function `v` the walk returns `M(v) = max(v.stacksize, v.stacksize + M(r))` over the callers
  `r` it explores. The estimate is `max M(s) + 1 + (largest public parameter count + 1)`.
- Recursion test: a caller that is already on the path (`symstack`, which holds the start function
  and the path) sets the recursion flag and **stops the loop over that function's callers**
  (`goto break_recursion`). So the result at a function depends on the path that led to it.
- Nothing is reused between paths. The walk visits every simple path of the call graph, from every
  start function. A layered graph with `w` functions per layer and `L` layers has `w^L` paths. A
  chain of `n` functions costs O(n³), because each recursion test scans `symstack` linearly.

Everything the estimate can print has to stay the same:

1. The number, printed in the stats block with `-d2`/`-d3`/`-v2`. The same number decides whether
   the block is printed at all (`stacksize+32 >= pc_stksize`).
2. "estimated max. usage: unknown, due to recursion". This appears only with `-R+`, because the
   flag is set only when `pc_recursion` is on (commit 1a608c2, 2016). Without `-R`, a recursive
   program still prints a number.
3. With `-R+`, one "recursion detected: function X ..." line per start function, plus a chain.
   The chain is whatever the walk left in `rsymstack` on its last path. It does not always contain
   the recursion: `stack_estimate_recursion_report.pwn` reports `Helper <- Walk <- Helper`, although
   only `Walk` calls itself, and "Ping directly calls itself", although Ping only calls Pong.
   That output is preserved as it is.

**Bug found in the original:** a self-recursive function can appear twice on one path (the walk
enters it again through its own caller entry). The path can therefore be longer than
`numfunctions+1`, the size of `symstack`/`rsymstack`, and the walk writes past the end of the heap
buffers. The fuzzer hit this in 15 of 400 random programs (`free(): invalid pointer`, SIGABRT), and
`tests/stack_estimate_self_recursion.pwn` crashes the original every time.

## The optimization

**Why a memo can be exact.** At function `v`, every function on the path reaches `v` through
caller edges. While the walk explores the callers above `v`, it can meet a path function `p` only
if `v` also reaches `p`, which puts `p` in the same strongly connected component (SCC) of the caller
graph as `v`. So the result below `v` (number, recursion flag, the walk's last path) depends only on
`path ∩ SCC(v)`. When the walk follows an edge into a **different** SCC, that intersection is empty:
the result does not depend on the path, so it can be computed once and reused.

Implementation, all in `sc1.c` inside `#if !defined SC_LIGHT`:

- A per-function side array (`stkfunc`). A function finds its entry through `sym->compound`, a
  field functions don't use (the old comment already described borrowing it). The field is reset
  to 0 afterwards.
- Tarjan's algorithm (`stack_scc`) numbers the SCCs in O(V+E).
- `max_stacksize_recurse` keeps the same loop and the same order. The recursion test now reads an
  O(1) `onpath` counter, which is incremented and decremented exactly where the old code pushed and
  popped `symstack`. A caller in another SCC is computed once (value and recursion flag) and then
  reused. A caller in the same SCC is walked as before.
- `recursion_chain()` rebuilds the chain the `-R` report prints. It replays only the last path of
  the old walk: in each function, the first caller already on the path, or else the last caller.
  It runs only when a recursion line is printed, and costs time in proportion to that line.
- `rsymstack` holds `2*numfunctions+2` entries, enough for a path that visits every function
  twice. `symstack` is gone.

## Complexity

- Tarjan: O(V+E) time, O(V) memory.
- Without recursion, every SCC is a single function. Each function's caller loop runs at most
  twice: once to fill its memo entry, once as a start function. That is **O(V+E)** in total. The
  benchmark counters confirm it: the walk makes `V + (number of memo fills) ≤ 2V` calls and at most
  2E memo lookups. Examples:
  - `layered 40x4`: V=161, E=628: 318 calls, 1240 lookups, where the old walk would need about
    4⁴⁰ paths;
  - `chain 5000`: 10001 calls, against 12.5 M calls (plus a linear scan per call) in the old walk.
- With recursion, the old path-by-path walk is kept inside each recursive SCC C. Each function of
  C starts at most two such walks (one memo fill, one as a start), and each walk stays inside C,
  because every edge that leaves C hits the memo. Inside one SCC the cost can still be exponential
  in the worst case, but it no longer multiplies across the whole graph. On the large real script this
  part costs 0.2 s (see below).
- Arithmetic: every number is the same path sum the original computed, so no new overflow is
  possible. The original's theoretical overflow (more than 2³¹ cells on one path) is unchanged.

## Verification

- **Differential fuzzing** (`fuzz.py`): random programs with up to 12-16 functions, random source
  order, self-calls, mutual recursion, unreached functions (which leave NULL caller slots) and
  public functions with 0-3 parameters. Each program is compiled under 4 flag sets: `-d3`,
  `-d3 -R+`, `-v2 -O0 -R+`, and the server's flags. stdout, stderr, exit code and `.amx` bytes
  are compared.
  - 400 programs against the original: the only differences were the 15 crashes of the original
    described above.
  - Against the original with only its two buffers enlarged (`oldfix`, the reference):
    2,100 programs, 8,400 compiles, **0 mismatches**. About 89 % of those programs contain
    recursion. The runs used Debug builds (asserts on) and Release builds.
- **Test suite** (`run_tests.py`, Debug and Release): 95 passed, 3 failed. The same 3 tests
  (`__pragma`, `__timestamp`, `gh_353_symbol_suggestions`) fail on unchanged master in the same
  container. New tests:
  - `stack_estimate_dag`: chain, shared callees, public function with parameters;
  - `stack_estimate_recursion_report`: the `-R+` lines, including the quirky chains;
  - `stack_estimate_recursion_quiet`: a number without `-R`;
  - `stack_estimate_self_recursion`: crashes the original;
  - `stack_estimate_wide_dag`: 4⁴⁰ paths, needs the memo.

  `run_tests.py` gained an optional `stdout_pattern` for `output_check` tests. The expected values
  come from the original compiler, except where it cannot finish or crashes; there they come from
  the buffer-fixed original or from the layered DP formula, which matches the original on smaller
  instances.
- **Large real script** (`main.pwn`, `omp-server:dev`, server flags without `-e`): it has 9,010 user functions and 21,731 caller edges: 8,802 SCCs, 213 functions on a cycle, and one recursive SCC of 204 functions.
  - stdout, stderr and the `.amx` are identical between the original and the new compiler, with and without `-R+`. The `-R+` output includes all 2,134 recursion lines.
  - Every run produced the same `.amx` (md5 `33d2f8a6855f`), including the original with `-e`.
- **Windows**: MSVC Win32 Release build (VS 2026, `/D sNAMEMAX=63`) with no warnings in sc1.c,
  and the same estimates as the Linux build on the new tests.

## Benchmarks

32-bit Linux builds (Release, `-m32 -DsNAMEMAX=63`, like CI) in Docker on the same PC. "estimate ms"
is CPU time inside `max_stacksize()`; "frames" counts calls of `max_stacksize_recurse()`. Both
come from a benchmark-only patch (`instrument.py`), which is not part of the change.

### Large real script (`main.pwn`, server flags `-;+ -(+ -\ -Z- -d3 -O0 -t4 -E`)

| compiler | wall | estimate (CPU) | walk calls | peak RSS |
|---|---|---|---|---|
| original, no `-e` | 1063 s (machine busy), 813 s with `-R+`; the user measured 521 s alone | 1051 s / 804 s | 10,717,940,338 | 32 MB |
| original, `-e` (estimate skipped) | 7.4 / 8.2 / 8.8 s | - | - | 32 MB |
| **new, no `-e`** | **7.8 / 9.0 / 9.2 s** | **0.20 s** (0.25 s with `-R+`) | 10,610,211 | 32 MB |

The new compiler runs the full estimate in about the time the original needs to skip it: the
difference is within run-to-run noise. All of its remaining 10.6 M walk calls come from the
path-by-path walk inside the 204-function recursive SCC.

The real filterscripts (19 `harness_*` scripts, at most 5 caller edges each) are too small to
time. Old and new give identical stdout and `.amx` on all of them.

### Synthetic programs (old timeout 600 s; flags `-;+ -(+ -d3 -O0`)

| program | V | E | largest SCC | old wall | old estimate | old walk calls | old `-e` wall | new wall | new estimate | new walk calls / memo lookups | `.amx` same |
|---|---|---|---|---|---|---|---|---|---|---|---|
| small (examples/gtkcalc.p) | 17 | 27 | 1 | 0.03 s | 0.0 ms | 104 | 0.03 s | 0.06 s | 0.0 ms | 30 / 50 | yes |
| layered 6x4 | 25 | 84 | 1 | 0.03 s | 0.1 ms | 12,733 | 0.03 s | 0.05 s | 0.0 ms | 46 / 152 | yes |
| layered 8x4 | 33 | 116 | 1 | 0.03 s | 3.1 ms | 203,877 | 0.03 s | 0.07 s | 0.1 ms | 62 / 216 | yes |
| layered 10x4 | 41 | 148 | 1 | 0.09 s | 65 ms | 3,262,221 | 0.03 s | 0.05 s | 0.0 ms | 78 / 280 | yes |
| layered 12x4 | 49 | 180 | 1 | 1.04 s | 1,007 ms | 52,195,765 | 0.02 s | 0.06 s | 0.0 ms | 94 / 344 | yes |
| layered 40x4 (4⁴⁰ paths) | 161 | 628 | 1 | > 600 s (timeout) | - | - | 0.03 s | 0.06 s | 0.0 ms | 318 / 1,240 | n/a |
| chain 500 | 501 | 500 | 1 | 0.07 s | 25 ms | 125,751 | 0.05 s | 0.08 s | 0.2 ms | 1,001 / 999 | yes |
| chain 2000 | 2001 | 2000 | 1 | 1.53 s | 1,411 ms | 2,003,001 | 0.21 s | 0.20 s | 0.4 ms | 4,001 / 3,999 | yes |
| chain 5000 | 5001 | 5000 | 1 | 23.9 s | 23,420 ms | 12,507,501 | 0.36 s | 0.53 s | 0.9 ms | 10,001 / 9,999 | yes |
| recursive 4 rings of 6 + layered 8x4 | 57 | 240 | 6 | 0.49 s | 461 ms | 16,544,097 | 0.04 s | 0.05 s | 0.0 ms | 374 / 436 | yes |
| recursive 4 rings of 6 + layered 40x4 | 185 | 752 | 6 | > 600 s (timeout) | - | - | 0.04 s | 0.13 s | 0.0 ms | 630 / 1,460 | n/a |

Peak RSS was 2-6 MB for every variant, with no difference between old and new. The extra memory
is O(V): one `stkfunc` entry (about 32 bytes) per function, plus two pointer arrays.

## Limitations

- Inside a large recursive SCC the walk is still path by path, so as to stay exact. A pathological
  program (a big, densely connected set of mutually recursive functions) can still be slow. The
  large real script's largest SCC (204 functions) costs 0.2 s.
- The walk and Tarjan's algorithm are recursive. Their depth is bounded by the longest call chain,
  as in the original.
- The quirks above are kept on purpose:
  - the `-R` chain text can name functions outside the recursion;
  - without `-R`, recursion prints a number instead of "unknown".

  Changing them would change the output.

## Files

- `source/compiler/sc1.c`: the change.
- `source/compiler/tests/run_tests.py`, `source/compiler/tests/stack_estimate_*`: the tests.
- `bench/stack-estimate/`:
  - `Dockerfile`, `build.sh`, `run.sh`: build environment;
  - `gen_layered.py`, `gen_chain.py`, `gen_recursive.py`: generators;
  - `fuzz.py`: differential fuzzer;
  - `bench.py`: synthetic benchmark; `real-script.sh`: runs on a real script;
  - `instrument.py`: benchmark counters.
