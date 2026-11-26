"""Real browser smoke, called only by the disposable Ubuntu integration job.

Tests Chrome desktop and mobile/tablet viewport emulation, not physical iOS.
The input is a temporary synthetic RTSP camera with a continuous audio tone.
"""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise SystemExit('requires a disposable GitHub-hosted runner')
    host,camera_id=sys.argv[1:]
    if host!='nvr.example.com' or not camera_id.isdigit():raise SystemExit('invalid browser fixture')
    origin='https://'+host
    def login(page,user,password):
        response=page.goto(origin,wait_until='domcontentloaded',timeout=30000)
        assert response and response.ok,'HTTPS page did not load'
        page.locator('#login-form [name=username]').fill(user)
        page.locator('#login-form [name=password]').fill(password)
        page.locator('#login-form button[type=submit]').click()
        expect(page.locator('#application')).to_be_visible(timeout=30000)
    def layout(page):
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'page overflows viewport'
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True,args=[f'--host-resolver-rules=MAP {host} 127.0.0.1','--no-proxy-server'])
        try:
            for width,height,mobile in ((1280,900,False),(390,844,True),(820,1180,True)):
                # No certificate-error bypass: the CI CA is enrolled in the
                # disposable user's NSS trust database by the parent script.
                context=browser.new_context(viewport={'width':width,'height':height},is_mobile=mobile,has_touch=mobile)
                try:
                    page=context.new_page();login(page,'ci-browser','ci-browser-password-123')
                    expect(page.locator('#show-admin')).to_be_hidden()
                    card=page.locator(f'.camera[data-camera-id="{camera_id}"]')
                    expect(card).to_be_visible(timeout=30000)
                    expect(page.locator('#cameras .camera')).to_have_count(1)
                    for control in ('#own-password-form','#exports-list','#layout-form','#delete-layout'):
                        expect(page.locator(control)).to_be_hidden()
                    page.locator('#show-settings').click();expect(page.locator('#settings-view')).to_be_visible();layout(page)
                    expect(page.locator('#own-password-form')).to_be_visible();expect(page.locator('#layout-form')).to_be_visible()
                    expect(page.locator('#cameras video-stream')).to_have_count(0)
                    page.locator('#show-cameras').click();expect(card).to_be_visible(timeout=30000)
                    card.locator('select').select_option('compatible')
                    # The custom element must supply a real decoded video, not
                    # merely establish a WebSocket or populate the DOM.
                    player=card.locator('video-stream')
                    player.wait_for(state='visible',timeout=30000)
                    page.wait_for_function('''id=>{const v=document.querySelector(`.camera[data-camera-id="${id}"] video-stream`)?.video;
                        return v&&v.videoWidth>0&&v.readyState>=2&&v.currentTime>0.2}''',arg=camera_id,timeout=60000)
                    layout(page)
                    if not mobile:
                        # Observe actual PCM output, independent of any UI label.
                        player.evaluate('''owner=>{const v=owner.video;const button=owner.closest('.camera').querySelector('button[aria-pressed]');
                            button.addEventListener('click',()=>{const ctx=new AudioContext();const source=ctx.createMediaElementSource(v);
                            const analyser=ctx.createAnalyser();source.connect(analyser);analyser.connect(ctx.destination);ctx.resume();
                            owner.ciAudio={ctx,analyser};},{once:true,capture:true});}''')
                        sound=card.locator('button[aria-pressed]');sound.click()
                        expect(sound).to_have_attribute('aria-pressed','true')
                        page.wait_for_function('''id=>{const p=document.querySelector(`.camera[data-camera-id="${id}"] video-stream`);
                            if(!p?.ciAudio||p.video.muted)return false;const a=p.ciAudio.analyser;
                            const samples=new Float32Array(a.fftSize);a.getFloatTimeDomainData(samples);
                            return samples.some(x=>Math.abs(x)>0.005)}''',arg=camera_id,timeout=30000)
                        sound.click();expect(sound).to_have_attribute('aria-pressed','false')
                    card.get_by_role('button',name='Archiwum').click()
                    expect(page.locator('#archive-view')).to_be_visible(timeout=30000);layout(page)
                    page.locator('#show-cameras').click();expect(card).to_be_visible(timeout=30000)
                    page.locator('#logout').click();expect(page.locator('#login-form')).to_be_visible(timeout=30000)
                finally:context.close()
                print(f'Browser viewport {width}x{height}: OK',flush=True)
            context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            try:
                page=context.new_page();login(page,'admin','ciAdm6')
                page.locator('#show-admin').click();expect(page.locator('#admin-view')).to_be_visible(timeout=30000);layout(page)
            finally:context.close()
        finally:browser.close()

if __name__=='__main__':main()
