"""Whole-homepage responsive regression gate, exercised in CI and after deploy.

This checks geometry and lifecycle behavior, rather than CSS source strings.
Screenshots still require visual inspection. No load is sent to production:
concurrent-viewer checks use only the local static server.
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright


def check_layout(page, width):
    return page.evaluate('''width => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      const centered = selector => Math.abs((rect(selector).left + rect(selector).right)/2 - width/2) < 2;
      const problems = [];
      for (const s of ['header', '.orpho-hero__lede', '.orpho-hero__copy', '#hero-envelope', '#doors', '.orpho-situations-wrap'])
        if (!centered(s)) problems.push(s + ' off center');
      for (const s of ['.orpho-hero__copy','.orpho-door__body','.orpho-situations__title'])
        if (getComputedStyle(document.querySelector(s)).textAlign !== 'center') problems.push(s + ' text alignment');
      if (document.documentElement.scrollWidth > width + 1) problems.push('document overflow');
      for (const el of document.querySelectorAll('header a, section h1, section h2, section h3, section p, section button, .orpho-door__cta')) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
        if (r.left < -1 || r.right > width+1) problems.push(el.className + ' outside viewport');
      }
      const cols = getComputedStyle(document.querySelector('.orpho-doors')).gridTemplateColumns.split(' ').length;
      if(cols !== (width <= 1040 ? 1 : 3)) problems.push('doors grid lost');
      return problems;
    }''', width)


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
        for width in [320,390,680,1024,1280,1920]:
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
            results.append({'width':width,'centered':True,'header_scrolls_away':True,'resize_and_hidden_recovery':True,'errors':errors})
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
