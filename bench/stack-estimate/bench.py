#!/usr/bin/env python3
"""Synthetic benchmark of the stack-size estimate: old vs new pawncc.

Runs INSIDE the pawncc-bench container (see run.sh):

  python3 /repo/bench/stack-estimate/bench.py --out /work/bench-synth [--timeout 600]

For every program three variants are compiled, each under /usr/bin/time:
  old     oldi-Release, estimate runs
  old -e  oldi-Release with -e<file> (estimate skipped: the baseline cost)
  new     newi-Release, estimate runs
Writes <out>/results.csv and <out>/results.md and prints the markdown.
"""
import argparse, csv, hashlib, os, re, shutil, signal, subprocess, sys, time

REPO = '/repo'
BIN = '/work/bin'
WORK = '/tmp/bench'          # container-local: 32-bit pawncc cannot stat bind-mount files
GEN = REPO + '/bench/stack-estimate/'


def gen(script, *args):
    """Run a generator script, return its bytes."""
    return subprocess.run([sys.executable, GEN + script, *args], check=True,
                          stdout=subprocess.PIPE).stdout


def programs():
    """List of (name, source path, extra flags)."""
    os.makedirs(WORK, exist_ok=True)
    progs = []
    # small: a real example. It is written without semicolons, so -;- -(-.
    # Examples and include dir are copied locally (bind-mount files cannot be stat'ed).
    ex = WORK + '/ex'
    shutil.rmtree(ex, ignore_errors=True)
    shutil.copytree(REPO + '/examples', ex)
    shutil.rmtree(WORK + '/include', ignore_errors=True)
    shutil.copytree(REPO + '/include', WORK + '/include')
    progs.append(('small gtkcalc.p', ex + '/gtkcalc.p', ['-;-', '-(-', '-i' + WORK + '/include']))
    std = ['-;+', '-(+']

    def add(name, script, *args):
        path = WORK + '/' + re.sub(r'\W', '_', name) + '.pwn'
        with open(path, 'wb') as f:
            f.write(gen(script, *args))
        progs.append((name, path, std))

    for L in (6, 8, 10, 12, 40):
        add('layered %dx4' % L, 'gen_layered.py', '--layers', str(L), '--width', '4')
    for n in (500, 2000, 5000):
        add('chain %d' % n, 'gen_chain.py', '--length', str(n))
    for L in (8, 40):
        add('recursive 4x6 +%dx4' % L, 'gen_recursive.py', '--rings', '4', '--ring-size', '6',
            '--layers', str(L), '--width', '4')
    return progs


STK = re.compile(r'STKEST (.*)')


def run(build, src, flags, timeout, amx, errfile=None):
    """Run one compile under /usr/bin/time. Returns a dict of measurements."""
    env = dict(os.environ, LD_LIBRARY_PATH='%s/%s' % (BIN, build))
    tfile = WORK + '/time.txt'
    for p in (tfile, amx):
        if os.path.exists(p):
            os.remove(p)
    cmd = ['/usr/bin/time', '-f', '%M', '-o', tfile, '%s/%s/pawncc' % (BIN, build), src] + flags + ['-o' + amx]
    if errfile:
        cmd.append('-e' + errfile)
    t0 = time.time()
    p = subprocess.Popen(cmd, env=env, cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    r = {'timeout': False}
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)   # kills time and pawncc
        p.communicate()
        r.update(timeout=True, wall=time.time() - t0, rc=None, rss=None, stdout=b'', stk={})
        return r
    r['wall'] = time.time() - t0
    r['rc'] = p.returncode
    r['stdout'] = out
    # max RSS is the last token of the time file (time may add a status line first)
    try:
        r['rss'] = int(open(tfile).read().split()[-1])
    except Exception:
        r['rss'] = None
    m = STK.search(err.decode(errors='replace'))
    r['stk'] = dict(kv.split('=') for kv in m.group(1).split()) if m else {}
    return r


def md5(path):
    try:
        return hashlib.md5(open(path, 'rb').read()).hexdigest()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--timeout', type=int, default=600)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(WORK + '/amx', exist_ok=True)
    rows = []
    for name, src, flags in programs():
        fl = flags + ['-d3', '-O0']
        key = re.sub(r'\W', '_', name)
        res = {}
        # new first, so the old variants can be compared with it
        for var, build, err in (('new', 'newi-Release', None), ('old', 'oldi-Release', None),
                                ('old -e', 'oldi-Release', WORK + '/err.txt')):
            amx = '%s/amx/%s.%s.amx' % (WORK, key, var.replace(' ', ''))
            r = run(build, src, fl, a.timeout, amx, err)
            r['md5'] = md5(amx) if not r['timeout'] else None
            res[var] = r
            print('  %s / %s: %s' % (name, var, 'TIMEOUT' if r['timeout'] else '%.2fs rc=%s' % (r['wall'], r['rc'])),
                  file=sys.stderr, flush=True)
        new = res['new']
        # graph size comes from new's line (old prints -1 for the SCC fields)
        g = new['stk'] or res['old']['stk'] or res['old -e']['stk']
        for var in ('old', 'old -e', 'new'):
            r = res[var]
            s = r['stk']
            if r['timeout'] or new['timeout']:
                same = 'n/a'
            elif var == 'new':
                same = '-'
            elif r['md5'] is None or new['md5'] is None:
                same = 'n/a'
            else:
                # -e sends compiler messages to a file, so its stdout is empty: compare the amx only
                out_ok = var == 'old -e' or r['stdout'] == new['stdout']
                same = 'yes' if r['md5'] == new['md5'] and out_ok else 'NO'

            def f(k, v=s):
                return '' if (k not in v or v[k] == '-1') else v[k]

            rows.append({
                'program': name, 'V': g.get('funcs', ''), 'E': g.get('edges', ''),
                'SCCs': f('sccs', g), 'largest SCC': f('maxscc', g), 'variant': var,
                'wall s': 'TIMEOUT (> %ds)' % a.timeout if r['timeout'] else '%.2f' % r['wall'],
                'estimate ms': f('time_ms'), 'frames': f('frames'),
                'memo lookups': f('lookups'), 'memo fills': f('fills'),
                'max RSS MB': '' if r['rss'] is None else '%.1f' % (r['rss'] / 1024.0),
                'exit': 'timeout' if r['timeout'] else r['rc'],
                'amx same as new': same,
            })
        oe = res['old -e']
        if not oe['timeout'] and not new['timeout'] and new['wall'] - oe['wall'] > 1.0:
            print('WARNING: new slower than old -e by %.2fs on %s' % (new['wall'] - oe['wall'], name), file=sys.stderr)
    cols = list(rows[0].keys())
    with open(a.out + '/results.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
    md = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    for r in rows:
        md.append('| ' + ' | '.join(str(r[c]) for c in cols) + ' |')
    open(a.out + '/results.md', 'w').write('\n'.join(md) + '\n')
    print('\n'.join(md))


if __name__ == '__main__':
    main()
