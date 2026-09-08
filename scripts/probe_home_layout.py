"""Whole-homepage responsive regression gate, exercised in CI and after deploy.

This checks geometry and lifecycle behavior, rather than CSS source strings.
Screenshots still require visual inspection. No load is sent to production:
concurrent-viewer checks use only the local static server.

Layout contract (2026-09-05, full-bleed): the FRAME is flush to the page rail
(--orpho-rail = clamp(1rem, 4vw, 4.5rem)) on both sides at every width, copy is
left-aligned and keeps a reading measure, the hero is two columns above 1040px
and one below, nothing escapes the viewport, and the document never scrolls
sideways. Phase C ornament (four corner guarantees with hairlines to the edge,
a receipt-to-block connector, a ruled ground) shows only above 1040px and never
overlaps the headline, copy, buttons, plate, receipt or toggle. The rail is recomputed here from the same clamp rather than read
back from CSS, so a sheet that silently drops the custom property fails.
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright

WIDTHS = [320, 390, 680, 1024, 1280, 1440, 1920, 2560]

# Content edge (border-box minus padding) must sit on the rail.
FRAMES = ['header', '.orpho-hero__inner', '#doors', '.action > .wrap',
          '.orpho-situations-wrap', '.orpho-pair', 'footer.site > .wrap']
# Border edge must sit on the rail (cards keep their own inner padding).
CARDS = ['.orpho-features-wrap', '.orpho-arch']


def check_layout(page, width):
    return page.evaluate('''([width, frames, cards]) => {
      const W = document.documentElement.clientWidth;
      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
      const rail = Math.min(Math.max(1 * rem, 0.04 * W), 4.5 * rem);
      const TOL = 2.5;
      const q = s => document.querySelector(s);
      const rect = s => q(s).getBoundingClientRect();
      const content = s => { const r = rect(s), cs = getComputedStyle(q(s));
        return { l: r.left + parseFloat(cs.paddingLeft), r: r.right - parseFloat(cs.paddingRight) }; };
      const onRail = (l, r) => Math.abs(l - rail) < TOL && Math.abs((W - r) - rail) < TOL;
      const problems = [];
      for (const s of frames) { const b = content(s); if (!onRail(b.l, b.r)) problems.push(`${s} not flush to rail (${b.l.toFixed(1)}..${(W - b.r).toFixed(1)} vs ${rail.toFixed(1)})`); }
      for (const s of cards) { const r = rect(s); if (!onRail(r.left, r.right)) problems.push(`${s} card not on rail (${r.left.toFixed(1)}..${(W - r.right).toFixed(1)} vs ${rail.toFixed(1)})`); }
      // The headline must start at the rail: proves legacy `.hero h1 { margin:0 auto }` is beaten.
      if (Math.abs(rect('.orpho-hero-title').left - rail) >= TOL) problems.push('hero title not at rail');
      for (const s of ['.orpho-hero__copy', '.orpho-hero-title', '.orpho-door__body', '.orpho-situations__title', '.orpho-step__body'])
        if (!['left', 'start'].includes(getComputedStyle(q(s)).textAlign)) problems.push(s + ' not left-aligned');
      // Copy keeps a measure at every width (60ch of the hero body face).
      if (rect('.orpho-hero__copy').width > 720) problems.push('hero copy exceeds its measure');
      const heroCols = getComputedStyle(q('.orpho-hero__inner')).gridTemplateColumns.split(' ').length;
      if (heroCols !== (W <= 1040 ? 1 : 2)) problems.push(`hero grid has ${heroCols} column(s) at ${W}`);
      if (document.documentElement.scrollWidth > W + 1) problems.push('document overflow');
      for (const el of document.querySelectorAll('header a, section h1, section h2, section h3, section p, section button, .orpho-door__cta')) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
        if (r.left < -1 || r.right > W + 1) problems.push(el.className + ' outside viewport');
      }
      const cols = getComputedStyle(q('.orpho-doors')).gridTemplateColumns.split(' ').length;
      if (cols !== (W <= 1040 ? 1 : 3)) problems.push('doors grid lost');
      // Phase C ornament: corners + connector above 1040 only, never touching the
      // hero content, hairlines reaching the viewport edge, ground texture painted.
      const corners = [...document.querySelectorAll('.orpho-corner')];
      const conn = q('.orpho-connector');
      const shown = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none'; };
      const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
      if (corners.length !== 4) problems.push(`expected 4 corners, found ${corners.length}`);
      if (W <= 1040) {
        if (corners.some(shown) || shown(conn)) problems.push('ornament visible in the one-column hero');
      } else {
        const hero = rect('.orpho-hero');
        // The tucked receipt keeps a layout box below the envelope while visibility:hidden; only visible boxes can collide.
        const content = ['.orpho-hero-title', '.orpho-hero__copy', '.orpho-hero__actions', '#hero-envelope', '#hero-envelope-toggle', '#hero-sample-receipt']
          .filter(s => getComputedStyle(q(s)).visibility !== 'hidden').map(s => rect(s));
        for (const c of corners) {
          const r = c.getBoundingClientRect();
          if (!shown(c)) { problems.push(c.className + ' hidden above 1040'); continue; }
          if (r.left < hero.left - 1 || r.right > hero.right + 1 || r.top < hero.top - 1 || r.bottom > hero.bottom + 1) problems.push(c.className + ' outside the hero');
          for (const [i, b] of content.entries()) if (hit(r, b)) problems.push(`${c.className} collides with content #${i}`);
          const onLeft = c.className.includes('--tl') || c.className.includes('--bl');
          if (Math.abs((onLeft ? r.left : W - r.right) - rail) >= TOL) problems.push(c.className + ' not at rail');
          const after = getComputedStyle(c, '::after');
          const hairline = parseFloat(after.width);
          if (Math.abs(hairline - (r.width + rail)) >= TOL) problems.push(`${c.className} hairline ${hairline.toFixed(1)} does not reach the edge (${(r.width + rail).toFixed(1)})`);
        }
        if (!shown(conn)) problems.push('connector hidden above 1040');
        else {
          const r = conn.getBoundingClientRect();
          if (Math.abs(W - r.right) >= 1) problems.push(`connector stops ${(W - r.right).toFixed(1)}px short of the edge`);
          if (!hit(r, rect('#hero-envelope'))) problems.push('connector does not reach the envelope plate');
          if (hit(r, rect('#hero-envelope-toggle'))) problems.push('connector crosses the toggle');
          for (const c of corners) if (hit(r, c.getBoundingClientRect())) problems.push('connector crosses a corner');
          if (parseInt(getComputedStyle(conn).zIndex, 10) !== 0) problems.push('connector must sit under the hero grid');
        }
        const ground = getComputedStyle(q('.orpho-hero'), '::before');
        if (!ground.backgroundImage.includes('gradient')) problems.push('hero ground texture missing');
      }
      return problems;
    }''', [width, FRAMES, CARDS])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url')
    parser.add_argument('--screenshots',type=Path)
    parser.add_argument('--browser',default='/Applications/Brave Browser.app/Contents/MacOS/Brave Browser')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root/'web')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    results=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(**({} if args.browser=='chromium' else {'executable_path':args.browser}))
        for width in WIDTHS:
            page=browser.new_page(viewport={'width':width,'height':900})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.add_init_script('''window.testHidden=false;
              Object.defineProperty(document,'hidden',{get:()=>window.testHidden});''')
            if not args.url:
                page.route('**/api/**',lambda route:route.fulfill(status=200,content_type='application/json',body='{}'))
            page.goto(args.url or f'http://127.0.0.1:{server.server_port}/',wait_until='load')
            page.evaluate('document.fonts.ready')
            page.wait_for_timeout(200)
            assert not check_layout(page,width),(width,check_layout(page,width))
            page.evaluate('scrollTo(0,500)')
            assert page.locator('header').bounding_box()['y']+page.locator('header').bounding_box()['height']<=0, 'header must leave viewport'
            if args.screenshots:
                args.screenshots.mkdir(exist_ok=True,parents=True)
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(args.screenshots/f'{width}-closed.png'),full_page=True)
                page.screenshot(path=str(args.screenshots/f'{width}-top.png'))
                page.locator('#doors').screenshot(path=str(args.screenshots/f'{width}-doors.png'))
            # Count only requests attributable to the interaction, not initial APIs.
            animation_requests=[]
            page.on('request',lambda req:animation_requests.append(req.url) if '/api/' in req.url and '/api/event' not in req.url else None)
            page.locator('#hero-envelope-toggle').click()
            page.wait_for_timeout(120)
            # Simulate background/minimize lifecycle; hidden state must release canvas.
            page.evaluate("testHidden=true;document.dispatchEvent(new Event('visibilitychange'))")
            assert page.locator('.orpho-genie-surface').count()==0
            page.evaluate("testHidden=false;document.dispatchEvent(new Event('visibilitychange'))")
            # Restore from a tiny window, then resize repeatedly during a return.
            page.set_viewport_size({'width':240,'height':240})
            page.set_viewport_size({'width':width,'height':900})
            page.wait_for_timeout(200)
            assert page.locator('#hero-sample-receipt').is_visible()
            assert not check_layout(page,width),(width,check_layout(page,width))
            page.locator('#hero-envelope-toggle').click()
            for w in [width-1,width-2,width]:page.set_viewport_size({'width':w,'height':900})
            page.wait_for_timeout(250)
            assert page.locator('.orpho-genie-surface').count()==0
            assert page.locator('#hero-envelope-toggle').get_attribute('aria-expanded')=='false'
            assert not errors,errors
            if not args.url: assert not animation_requests,animation_requests
            results.append({'width':width,'flush_to_rail':True,'header_scrolls_away':True,'resize_and_hidden_recovery':True,'errors':errors})
            page.close()
        # Multiple visitors have isolated browser state. This is a client smoke
        # check, not a claim about maximum production request throughput.
        if not args.url:
            pages=[browser.new_page(viewport={'width':390,'height':800}) for _ in range(8)]
            for page in pages:
                page.route('**/api/**',lambda route:route.fulfill(status=200,content_type='application/json',body='{}'))
                page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='load')
            for page in pages:page.locator('#hero-envelope-toggle').dispatch_event('click')
            for page in pages:
                page.wait_for_function("!document.querySelector('.orpho-genie-surface') && document.querySelector('#hero-envelope-toggle').getAttribute('aria-expanded') === 'true'", timeout=10000)
                assert page.locator('#hero-sample-receipt').is_visible()
                assert page.locator('.orpho-genie-surface').count()==0
                page.close()
        browser.close()
    server.shutdown()
    print(json.dumps({'scenarios':results,'isolated_viewers':8 if not args.url else None},indent=2))


if __name__=='__main__':main()
