#!/usr/bin/env python3
"""Add benchmark counters to sc1.c (old or new stack estimate); never commit the result.

  instrument.py <sc1.c in> <sc1.c out>

The instrumented compiler prints one line to stderr after the stack estimate:

  STKEST time_ms=<cpu ms> frames=<calls of max_stacksize_recurse> lookups=<memo lookups>
         fills=<memo fills> funcs=<V> edges=<E> sccs=<n> cyclic=<functions on a cycle> maxscc=<largest SCC>

lookups/fills/sccs/cyclic/maxscc are -1 for the old code (it has no memo and no SCCs).
"""
import sys

def sub(src, old, new, count=1):
    n = src.count(old)
    if n != count:
        sys.exit('instrument.py: expected %d match(es) of %r, found %d' % (count, old, n))
    return src.replace(old, new)

def main():
    src = open(sys.argv[1], newline='').read()
    is_new = 'stkfunc' in src

    src = sub(src, 'static long max_stacksize(symbol *root,int *recursion);\n',
              'static long max_stacksize(symbol *root,int *recursion);\n'
              'static unsigned long long stkest_frames,stkest_lookups,stkest_fills;\n'
              'static int stkest_funcs,stkest_edges,stkest_sccs=-1,stkest_cyclic=-1,stkest_maxscc=-1;\n')
    src = sub(src, '      long stacksize=max_stacksize(&glbtab,&recursion);\n',
              '      clock_t stkest_t0=clock();\n'
              '      long stacksize=max_stacksize(&glbtab,&recursion);\n'
              '      fprintf(stderr,"STKEST time_ms=%.1f frames=%llu lookups=%lld fills=%lld funcs=%d edges=%d'
              ' sccs=%d cyclic=%d maxscc=%d\\n",(double)(clock()-stkest_t0)*1000.0/CLOCKS_PER_SEC,'
              'stkest_frames,LOOKUPS,FILLS,stkest_funcs,stkest_edges,stkest_sccs,stkest_cyclic,stkest_maxscc);\n'
              .replace('LOOKUPS', '(long long)stkest_lookups' if is_new else '-1LL')
              .replace('FILLS', '(long long)stkest_fills' if is_new else '-1LL'))
    src = sub(src, '  maxsize=sym->x.stacksize;\n', '  stkest_frames++;\n  maxsize=sym->x.stacksize;\n')
    src = sub(src, '  maxsize=0;\n  maxparams=0;\n',
              '  { symbol *s_; int i_; stkest_funcs=stkest_edges=0;\n'
              '    for (s_=root->next; s_!=NULL; s_=s_->next)\n'
              '      if (s_->ident==iFUNCTN && (s_->usage & uNATIVE)==0) {\n'
              '        stkest_funcs++;\n'
              '        for (i_=0; i_<s_->numrefers; i_++) if (s_->refer[i_]!=NULL) stkest_edges++;\n'
              '      }\n  }\n'
              '  maxsize=0;\n  maxparams=0;\n')
    if is_new:
        src = sub(src, '      if (!r->memo) {\n', '      stkest_lookups++;\n      if (!r->memo) {\n')
        src = sub(src, '        r->size=max_stacksize_recurse(info,ref,0L,pubfuncparams,&rec);\n',
                  '        stkest_fills++;\n        r->size=max_stacksize_recurse(info,ref,0L,pubfuncparams,&rec);\n')
        src = sub(src, '  free((void*)sccstack);\n',
                  '  free((void*)sccstack);\n'
                  '  { int *n_=(int*)calloc(numscc+1,sizeof(int)); int k_,i_;\n'
                  '    stkest_sccs=numscc; stkest_cyclic=0; stkest_maxscc=0;\n'
                  '    for (k_=0; k_<numfunctions; k_++) n_[info[k_].scc]++;\n'
                  '    for (k_=0; k_<numfunctions; k_++) {\n'
                  '      int self_=0; symbol *s_=info[k_].sym;\n'
                  '      for (i_=0; i_<s_->numrefers; i_++) if (s_->refer[i_]==s_) self_=1;\n'
                  '      if (n_[info[k_].scc]>1 || self_) stkest_cyclic++;\n'
                  '      if (n_[info[k_].scc]>stkest_maxscc) stkest_maxscc=n_[info[k_].scc];\n'
                  '    }\n    free(n_);\n  }\n')
    open(sys.argv[2], 'w', newline='').write(src)

if __name__ == '__main__':
    main()
