"""Real-browser visual and lifecycle proof for the continuous receipt surface.

Requires Playwright and a local Chromium browser. --url checks a released site;
without it a loopback server serves this worktree. Visual frames still require
human inspection: passing assertions alone never establishes aesthetic quality.
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright


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
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True)
            for width,mode in [(1280,'normal'),(390,'normal'),(680,'normal'),(390,'reduce'),(390,'unavailable'),(390,'failure')]:
                page=browser.new_page(viewport={'width':width,'height':1050},reduced_motion='reduce' if mode=='reduce' else 'no-preference')
                page.add_init_script('''window.frozenFrames=[];const nativeRAF=requestAnimationFrame;
                  window.requestAnimationFrame=cb=>nativeRAF(t=>{if(window.freezeFrames)frozenFrames.push(cb);else cb(t)});
                  window.freezeScene=()=>{freezeFrames=true;window.frozenAnimations=document.getAnimations();frozenAnimations.forEach(a=>a.pause())};
                  window.resumeScene=()=>{freezeFrames=false;frozenAnimations.forEach(a=>a.play());frozenFrames.splice(0).forEach(cb=>nativeRAF(cb))};''')
                if mode=='unavailable':
                    page.add_init_script('HTMLCanvasElement.prototype.getContext=()=>null')
                if mode=='failure':
                    page.add_init_script('''const draw=CanvasRenderingContext2D.prototype.drawImage;
                      CanvasRenderingContext2D.prototype.drawImage=function(...args){
                        if(this.canvas.classList.contains('orpho-genie-surface'))throw new Error('draw unavailable');
                        return draw.apply(this,args)}''')
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(args.url or f'http://127.0.0.1:{server.server_port}/index.html',wait_until='load')
                page.evaluate("scrollTo(0,document.querySelector('#hero-envelope').getBoundingClientRect().top+scrollY-170)")
                page.wait_for_timeout(500)
                before=page.evaluate('scrollY')
                if args.screenshots:
                    args.screenshots.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(args.screenshots/f'{width}-{mode}-closed.png'))
                page.locator('#hero-envelope-toggle').click()
                page.wait_for_timeout(400)
                if mode=='normal':
                    assert page.locator('.orpho-genie-surface').count()==1
                    assert page.locator('.orpho-genie__band').count()==0
                    if args.screenshots:
                        page.evaluate('freezeScene()');page.wait_for_timeout(40)
                        page.screenshot(path=str(args.screenshots/f'{width}-{mode}-opening.png'))
                        page.evaluate('resumeScene()')
                page.wait_for_timeout(1600)
                assert page.locator('#hero-envelope-toggle').get_attribute('aria-expanded')=='true'
                assert page.locator('.orpho-genie-surface').count()==0
                assert page.locator('#hero-sample-receipt').is_visible()
                delta=page.evaluate('scrollY')-before
                assert abs(delta)<=1,('scroll jump',width,mode,delta)
                if args.screenshots:
                    page.screenshot(path=str(args.screenshots/f'{width}-{mode}-open.png'))
                page.locator('#hero-envelope-toggle').click()
                page.wait_for_timeout(400)
                if mode=='normal' and args.screenshots:
                    page.evaluate('freezeScene()');page.wait_for_timeout(40)
                    page.screenshot(path=str(args.screenshots/f'{width}-{mode}-closing.png'))
                    page.evaluate('resumeScene()')
                page.wait_for_timeout(1600)
                assert page.locator('#hero-envelope-toggle').get_attribute('aria-expanded')=='false'
                assert page.locator('.orpho-genie-surface').count()==0
                # Reverse immediately without restarting from either endpoint.
                page.locator('#hero-envelope-toggle').click();page.wait_for_timeout(180)
                page.locator('#hero-envelope-toggle').dispatch_event('click');page.wait_for_timeout(1600)
                assert page.locator('#hero-envelope-toggle').get_attribute('aria-expanded')=='false'
                assert page.locator('.orpho-genie-surface').count()==0
                page.locator('#hero-envelope-toggle').click();page.wait_for_timeout(200)
                page.set_viewport_size({'width':350,'height':1050});page.wait_for_timeout(1400)
                assert page.locator('#hero-sample-receipt').is_visible()
                assert page.locator('.orpho-genie-surface').count()==0
                assert not errors,errors
                results.append({'width':width,'mode':mode,'scroll_delta':delta,'errors':errors,'passed':True})
                page.close()
            browser.close()
        print(json.dumps(results,indent=2))
    finally:
        server.shutdown()


if __name__=='__main__':
    main()
