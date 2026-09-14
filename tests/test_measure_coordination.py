"""Bind the browser probe's independent caps to their stylesheet declarations."""
import ast
import re
import unittest
from pathlib import Path
from _css import strip_comments

ROOT = Path(__file__).resolve().parents[1]


def declared_cap(css, selector, tokens):
    rules = re.findall(re.escape(selector) + r'\s*\{([^{}]*)\}', strip_comments(css))
    values = [v.strip() for body in rules for v in re.findall(r'max-width\s*:\s*([^;]+)', body)]
    if len(values) != 1:
        raise ValueError(f'{selector}: expected one cap, got {values}')
    value = values[0]
    var = re.fullmatch(r'var\((--[\w-]+)\)', value)
    if var:
        matches = re.findall(re.escape(var[1]) + r'\s*:\s*([\d.]+)px\s*;', strip_comments(tokens))
        if len(matches) != 1:
            raise ValueError(f'{var[1]}: missing or ambiguous token')
        return float(matches[0])
    px = re.fullmatch(r'([\d.]+)px', value)
    if not px:
        raise ValueError(f'unsupported cap: {value}')
    return float(px[1])


class TestMeasureCoordination(unittest.TestCase):
    def test_probe_caps_match_css(self):
        tree = ast.parse((ROOT / 'scripts/probe_home_layout.py').read_text())
        measured = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'MEASURED' for t in n.targets)]
        self.assertEqual(len(measured), 1)
        self.assertEqual(len(measured[0]), 3)
        css = ROOT / 'web/css'
        tokens = (css / 'orpho-tokens.css').read_text()
        sheets = {'.orpho-arch__layers': 'orpho-home.css', '.orpho-features': 'orpho-primitives.css',
                  '.sample-receipt-card': '../index.css'}
        self.assertEqual({x['inner'] for x in measured[0]}, set(sheets))
        for item in measured[0]:
            source = (css / sheets[item['inner']]).read_text()
            cap = declared_cap(source, item['inner'], tokens)
            self.assertEqual(item['measure'], cap, item['inner'])
            # Plant the actual drift in each declaration source, not in a fixture unrelated to it.
            if item['inner'] == '.orpho-features':
                mutant = tokens.replace('--orpho-measure: 1180px;', '--orpho-measure: 1181px;')
                self.assertNotEqual(item['measure'], declared_cap(source, item['inner'], mutant))
            else:
                mutant = re.sub(r'(max-width\s*:\s*)' + str(int(cap)) + r'px;',
                                lambda m: m[1] + str(int(cap) + 1) + 'px;', source)
                self.assertNotEqual(item['measure'], declared_cap(mutant, item['inner'], tokens))

    def test_missing_ambiguous_and_unsupported_caps_fail(self):
        for css, tokens in [('', ''), ('.x { max-width: 1px; } .x { max-width: 2px; }', ''),
                            ('.x { max-width: none; }', ''), ('.x { max-width: var(--x); }', '')]:
            with self.subTest(css=css), self.assertRaises(ValueError):
                declared_cap(css, '.x', tokens)
