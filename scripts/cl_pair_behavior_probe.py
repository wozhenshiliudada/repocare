"""Fixed public-invariant checks; target imports occur only inside owned containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parent))
from swebench_cl_pair_specs import KIND,PAIRS

def case(name):
    import sympy
    assert str(Path(sympy.__file__).resolve()).startswith('/testbed/sympy/'), 'Unexpected SymPy import origin'
    from sympy import Float,I,Rational,Symbol,srepr
    x,y=Symbol('x'),Symbol('y')
    if name=='implemented_single_call':
        from sympy.utilities.lambdify import implemented_function
        f=implemented_function('owned_f',lambda value:value**2)
        assert f(2).evalf()==Float(4)
    elif name=='rational_precision':
        assert abs(Rational(1,3).evalf(30)*3-1)<Float('1e-28')
    elif name=='complex_and_symbolic':
        assert abs(complex((1+I).evalf())-(1+1j))<1e-12
        assert x.evalf()==x
    elif name=='repr_list_tuple':
        assert srepr([x,y])=="[Symbol('x'), Symbol('y')]"
        assert srepr((x,y))=="(Symbol('x'), Symbol('y'))"
    elif name=='str_rational_settings':
        from sympy.printing.str import sstr
        assert sstr(x+Rational(1,2),sympy_integers=True)=='x + S(1)/2'
    elif name=='repr_rational':
        assert srepr(Rational(1,2))=='Rational(1, 2)'
    else:raise ValueError('Unknown fixed case')

def probe(pair,base):
    spec=PAIRS[pair]
    if base not in spec['bases'].values():raise ValueError('Unexpected pair base')
    env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',GIT_TERMINAL_PROMPT='0')
    def git(*args):return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-C','/testbed',*args],env=env,stderr=subprocess.PIPE,timeout=30)
    git('checkout','--detach',base)
    if git('rev-parse','HEAD').decode().strip()!=base:raise ValueError('Container base mismatch')
    patch=Path('/probe/patch.diff').read_bytes()
    if patch:git('apply','--check','/probe/patch.diff');git('apply','/probe/patch.diff')
    changed=set(git('diff','--name-only','-z','HEAD').decode().strip('\0').split('\0'))-{''}
    if not changed<=set(spec['allowed_paths']):raise ValueError('Patch exceeds selected source scope')
    sys.path.insert(0,'/testbed')
    rows=[]
    for name in spec['cases']:
        try:case(name)
        except Exception:rows.append({'case':name,'passed':False,'diagnostic':traceback.format_exc()[-4000:]})
        else:rows.append({'case':name,'passed':True,'diagnostic':''})
    return {'kind':KIND,'pair':pair,'base_commit':base,'patch_sha256':hashlib.sha256(patch).hexdigest(),
            'cases':rows,'passed':sum(row['passed'] for row in rows),'total':len(rows),
            'model_requests':0,'official_score':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--pair',choices=PAIRS,required=True);parser.add_argument('--base',required=True)
    args=parser.parse_args()
    try:
        value=probe(args.pair,args.base);code=0 if value['passed']==value['total'] else 1
    except Exception as error:value={'kind':KIND,'infrastructure_error':True,'error':type(error).__name__+': '+str(error),'model_requests':0};code=2
    print(json.dumps(value),flush=True);raise SystemExit(code)
