"""Two disjoint public development pairs; selection used issue text, not gold diffs."""
PAIRS={
 'evalf':{'history':'sympy__sympy-12096','target':'sympy__sympy-13372',
  'bases':{'sympy__sympy-12096':'d7c3045115693e887bcd03599b7ca4650ac5f2cb',
           'sympy__sympy-13372':'30379ea6e225e37833a764ac2da7b7fadf5fe374'},
  'allowed_paths':['sympy/core/function.py','sympy/core/evalf.py'],
  'readonly_paths':['sympy/core/mul.py','sympy/utilities/lambdify.py'],
  'cases':['implemented_single_call','rational_precision','complex_and_symbolic']},
 'printing':{'history':'sympy__sympy-14531','target':'sympy__sympy-19346',
  'bases':{'sympy__sympy-14531':'205da797006360fc629110937e39a19c9561313e',
           'sympy__sympy-19346':'94fb720696f5f5d12bad8bc813699fd696afd2fb'},
  'allowed_paths':['sympy/printing/str.py','sympy/printing/repr.py','sympy/printing/python.py'],
  'readonly_paths':['sympy/printing/printer.py','sympy/printing/precedence.py'],
  'cases':['repr_list_tuple','str_rational_settings','repr_rational']}}
KIND='swebench-cl-pair-preservation-v1'
