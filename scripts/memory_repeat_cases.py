"""Authored stateful-printer transfer fixtures, not SWE-bench tasks or scores."""
KIND = 'authored-printer-repeat-error-v1'
ARMS = ('A_no_history', 'B_selected_case', 'C_retrieved_cases')
RELATED = 'printer-double-dispatch'
BASE = '''import keyword

class Symbol:
    def __init__(self, name):
        self.name = name

class Group:
    def __init__(self, *values):
        self.values = values

class PythonPrinter:
    def __init__(self):
        self.symbols = []
        self.seen = set()

    def _name(self, name):
        return name

    def _render(self, expr):
        if isinstance(expr, Symbol):
            name = self._name(expr.name)
            if name not in self.seen:
                self.symbols.append((name, expr.name))
                self.seen.add(name)
            return name
        if isinstance(expr, (int, float)):
            return repr(expr)
        raise NotImplementedError(type(expr).__name__)

    def doprint(self, expr):
        self.seen = set()
        return self._render(expr)

    def generate(self, expr):
        expression = self.doprint(expr)
        declarations = ["%s = Symbol(%r)" % item for item in self.symbols]
        return "\\n".join(declarations + ["e = " + expression])

def emit(expr):
    return PythonPrinter().generate(expr)

def emit_many(expressions):
    return [emit(expressions[0]) for _ in expressions]
'''
GROUP = '''        if isinstance(expr, Group):
            return "group(" + ", ".join(self._render(value) for value in expr.values) + ")"
'''

def fixture(case, variant='base'):
    if case not in CASES or variant not in ('base', 'good', 'bad'):
        raise ValueError('Unknown authored fixture')
    value = BASE
    if variant != 'base':
        if case == 'nested_group':
            value = value.replace('        raise NotImplementedError(type(expr).__name__)',
                                  GROUP + '        raise NotImplementedError(type(expr).__name__)')
        elif case == 'keyword_names':
            value = value.replace('        return name\n',
                                  '        return name + "_" if keyword.iskeyword(name) else name\n')
        else:
            value = value.replace('return [emit(expressions[0]) for _ in expressions]',
                                  'return [emit(expr) for expr in expressions]')
    if variant == 'bad':
        if case == 'batch_requests':
            value = value.replace('    return [emit(expr) for expr in expressions]',
                                  '    printer = PythonPrinter()\n    return [printer.generate(expr) for expr in expressions]')
        else:
            value = value.replace('        expression = self.doprint(expr)\n',
                                  '        expression = self.doprint(expr)\n        expression = self.doprint(expr)\n')
    return value

FEATURES = {
 'nested_group': '''    def test_group(self):
        program = emit(Group(Symbol('x'), 2))
        self.assertEqual(program.splitlines()[-1], 'e = group(x, 2)')
        self.assertIn("x = Symbol('x')", program.splitlines())
    def test_nested(self):
        program = emit(Group(1, Group(Symbol('y'), 3)))
        self.assertEqual(program.splitlines()[-1], 'e = group(1, group(y, 3))')
        self.assertIn("y = Symbol('y')", program.splitlines())
''',
 'keyword_names': '''    def test_keyword(self):
        program = emit(Symbol('for'))
        self.assertEqual(program.splitlines()[-1], 'e = for_')
        self.assertIn("for_ = Symbol('for')", program.splitlines())
    def test_second_keyword(self):
        program = emit(Symbol('lambda'))
        self.assertEqual(program.splitlines()[-1], 'e = lambda_')
        self.assertIn("lambda_ = Symbol('lambda')", program.splitlines())
''',
 'batch_requests': '''    def test_distinct_values(self):
        self.assertEqual([value.splitlines()[-1] for value in emit_many([1, 2])], ['e = 1', 'e = 2'])
        programs = emit_many([Symbol('x'), Symbol('y')])
        self.assertEqual([value.splitlines()[-1] for value in programs], ['e = x', 'e = y'])
        self.assertIn("x = Symbol('x')", programs[0].splitlines())
        self.assertIn("y = Symbol('y')", programs[1].splitlines())
    def test_empty(self):
        self.assertEqual(emit_many([]), [])
'''}
CASES = {
 'nested_group': {'issue': 'Python printer: support Group and nested Group expressions. Render as group(x, 2) or group(1, group(y, 3)); include the symbol declarations needed by the expression and keep existing outputs working.',
                  'order': ARMS},
 'keyword_names': {'issue': 'Python printer: support symbol names that are Python keywords. Use a trailing underscore in emitted variable identifiers, for example for_ and lambda_, while retaining the original Symbol name in its declaration. Keep ordinary symbols and numbers working.',
                   'order': (ARMS[1], ARMS[2], ARMS[0])},
 'batch_requests': {'issue': 'Python printer: emit_many must return one independent generated program for each input expression, in input order. Currently different inputs produce the first program repeatedly. Keep emit and the empty input working.',
                    'order': (ARMS[2], ARMS[0], ARMS[1])}}

def feature_tests(case):
    return ('import unittest\nfrom printer import Symbol, Group, emit, emit_many\n'
            'class FeatureChecks(unittest.TestCase):\n' + FEATURES[case])

# Externally executed preservation checks; never put into the Agent source view.
PRESERVATION = '''# PRIVATE_REPEAT_CHECK_CANARY
class PreservationChecks(unittest.TestCase):
    def test_number(self):
        self.assertEqual(emit(3), 'e = 3')
    def test_symbol(self):
        self.assertEqual(emit(Symbol('x')), "x = Symbol('x')\\ne = x")
    def test_independent_calls(self):
        self.assertEqual(emit(Symbol('x')), emit(Symbol('x')))
    def test_unique_declarations(self):
        programs = emit_many([Symbol('x'), Symbol('x')]) if CASE == 'batch_requests' else [emit(Symbol('x'))]
        for program in programs:
            declarations = [line.split(' = ', 1)[0] for line in program.splitlines() if ' = Symbol(' in line]
            self.assertEqual(len(declarations), len(set(declarations)), 'duplicate declarations: stateful printer collected an existing symbol again')
'''

REFERENCE_NOTES = {
 'release-checklist': 'Authored reference, not an Agent trajectory. Before a package release, record the version, changelog and artifact hashes. Publication needs its own authorization.',
 'dependency-updates': 'Authored reference, not an Agent trajectory. A dependency update should record its declared version range and compatibility checks. A lockfile describes a resolved dependency graph.',
 'ci-triage': 'Authored reference, not an Agent trajectory. CI environment errors and code assertions are different observations. Preserve original exit codes, timeouts and logs.',
 'git-checkpoints': 'Authored reference, not an Agent trajectory. Use an isolated worktree with a fixed commit before a repair. A historical approval does not authorize a new edit.'}
