"""Accessible content and dependency contracts; visual proof is browser-based."""
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class Elements(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if attrs.get('id'): self.ids[attrs['id']]=(tag,attrs)

def test_single_native_control_describes_the_real_receipt():
    parser=Elements();parser.feed((ROOT/'web/index.html').read_text())
    tag,attrs=parser.ids['hero-envelope-toggle']
    assert tag=='button' and attrs['type']=='button'
    assert attrs['aria-controls']=='hero-sample-receipt'
    assert attrs['aria-expanded']=='false'
    assert parser.ids['hero-sample-receipt'][0]=='article'

def test_original_sample_stays_in_html_without_javascript():
    html=(ROOT/'web/index.html').read_text()
    assert html.count('id="hero-sample-receipt"')==1
    assert '7accf9e90453280e6fb081fd9d83dfb1eeef3bd64e0d680826989ba79bccac88' in html
    assert 'class="orpho-hero__plate"' in html

def test_script_parses_and_has_no_runtime_dependency():
    subprocess.run(['node','--check',str(ROOT/'web/hero-envelope.js')],check=True)
    script=(ROOT/'web/hero-envelope.js').read_text()
    assert 'cloneNode' not in script
    assert 'fetch(' not in script
    assert 'innerHTML' not in script

def test_actual_handlers_recover_and_preserve_accessible_state():
    subprocess.run(['node','tests/helpers/hero_lifecycle.cjs'],cwd=ROOT,check=True)
