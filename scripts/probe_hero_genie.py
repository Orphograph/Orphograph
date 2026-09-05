"""Reproducible Chromium/Brave hero lifecycle proof; requires playwright.
Run: python scripts/probe_hero_genie.py [--browser /path/to/chromium]
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', help='Public release URL; defaults to the local worktree')
    parser.add_argument('--screenshots', type=Path, help='Optional output directory for phone frames')
    parser.add_argument('--browser', default='/Applications/Brave Browser.app/Contents/MacOS/Brave Browser')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root / 'web'))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.browser, headless=True)
            for width, mode in [(1280, 'warp'), (390, 'warp'), (680, 'warp'), (390, 'fallback'), (390, 'reduce'), (390, 'failure')]:
                page = browser.new_page(viewport={'width': width, 'height': 900}, reduced_motion='reduce' if mode == 'reduce' else 'no-preference')
                if mode == 'failure':
                    page.add_init_script('Element.prototype.animate = () => { throw new Error("WAAPI unavailable") }')
                if mode == 'fallback':
                    page.add_init_script('Element.prototype.animate = undefined')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                cdp = page.context.new_cdp_session(page)
                cdp.send('Profiler.enable')
                cdp.send('Profiler.startPreciseCoverage', {'callCount': True, 'detailed': True})
                page.goto(args.url or f'http://127.0.0.1:{server.server_port}/index.html', wait_until='load')
                page.evaluate("scrollTo(0, document.querySelector('#hero-envelope').getBoundingClientRect().top + scrollY - 170)")
                page.wait_for_timeout(500)
                before = page.evaluate('scrollY')
                if args.screenshots and width == 390 and mode == 'warp':
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshots / 'phone-closed.png'))
                page.evaluate("window.frameDeltas=[]; let last=performance.now(), end=last+1400; requestAnimationFrame(function tick(now){frameDeltas.push(now-last);last=now;if(now<end)requestAnimationFrame(tick)})")
                page.locator('#hero-envelope-toggle').click()
                page.wait_for_timeout(300)
                assert page.locator('.orpho-genie').count() == (1 if mode == 'warp' else 0)
                if args.screenshots and width == 390 and mode == 'warp':
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    page.evaluate("document.querySelectorAll('.orpho-genie, .orpho-genie__band').forEach(el => el.getAnimations().forEach(a => {a.pause(); a.currentTime = 300}))")
                    page.screenshot(path=str(args.screenshots / 'phone-flight.png'))
                    page.evaluate("document.querySelectorAll('.orpho-genie, .orpho-genie__band').forEach(el => el.getAnimations().forEach(a => a.play()))")
                page.wait_for_timeout(1200)
                assert page.locator('#hero-envelope-toggle').get_attribute('aria-expanded') == 'true'
                page.wait_for_function("!document.querySelector('.orpho-genie')", timeout=10000)
                assert page.locator('#hero-sample-receipt').is_visible()
                jump = page.evaluate('scrollY') - before
                assert abs(jump) <= 1, (width, mode, 'unexpected scroll jump', jump)
                if args.screenshots and width == 390 and mode == 'warp':
                    page.screenshot(path=str(args.screenshots / 'phone-open.png'))
                # Resize open and ensure the newly wrapped letter fits.
                page.set_viewport_size({'width': 350, 'height': 900})
                page.wait_for_timeout(900)
                fits = page.evaluate('''() => {const p=document.querySelector('#hero-envelope').getBoundingClientRect(); const r=document.querySelector('#hero-sample-receipt').getBoundingClientRect(); return r.bottom <= p.bottom + 1}''')
                assert fits, (width, mode, 'receipt overflows resized plate')
                page.locator('#hero-envelope-toggle').click()
                page.wait_for_timeout(1400)
                assert page.locator('#hero-envelope-toggle').get_attribute('aria-expanded') == 'false'
                page.wait_for_function("!document.querySelector('.orpho-genie')", timeout=10000)
                # Resize during a flight cancels obsolete geometry cleanly.
                page.locator('#hero-envelope-toggle').click()
                page.wait_for_timeout(200)
                page.set_viewport_size({'width': 410, 'height': 900})
                page.wait_for_timeout(1200)
                page.wait_for_function("!document.querySelector('.orpho-genie')", timeout=10000)
                assert page.locator('#hero-sample-receipt').is_visible()
                assert not errors, errors
                coverage = cdp.send('Profiler.takePreciseCoverage')['result']
                hero = next(item for item in coverage if '/hero-envelope.js?' in item['url'])
                ranges = [r for fn in hero['functions'] for r in fn['ranges']]
                measured = bytearray(max(r['endOffset'] for r in ranges))
                for r in sorted(ranges, key=lambda r: r['endOffset'] - r['startOffset'], reverse=True):
                    measured[r['startOffset']:r['endOffset']] = bytes([int(r['count'] > 0)]) * (r['endOffset']-r['startOffset'])
                if 'covered' not in locals():
                    covered = measured
                else:
                    covered = bytearray(a or b for a,b in zip(covered, measured))
                results.append({'width': width, 'mode': mode, 'scroll_delta': jump, 'resize_fits': fits, 'page_errors': errors, 'frame_intervals_ms': page.evaluate('({median: frameDeltas.sort((a,b)=>a-b)[Math.floor(frameDeltas.length/2)], p95:frameDeltas[Math.floor(frameDeltas.length*.95)]})')})
                page.close()
            browser.close()
        print(json.dumps({'scenarios': results, 'script_byte_coverage_percent': round(100*sum(covered)/len(covered), 2)}, indent=2))
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
