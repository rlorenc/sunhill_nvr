"""Test multi-camera archive layout and real MP4 playback with mocked archive API."""
import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / 'src/sunhill_nvr/static'
with tempfile.TemporaryDirectory() as directory:
    sample = Path(directory) / 'sample.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10','-t','15','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(sample)], check=True)
    data=sample.read_bytes()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('SUNHILL_BROWSER_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1800,'height':1000}); errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        html=re.sub(r'<script\b[^>]*>.*?</script>','',(ROOT/'index.html').read_text(),flags=re.S)
        page.set_content(html);page.add_style_tag(content=(ROOT/'app.css').read_text())
        # Replace only the test media URL, not the playback or layout logic.
        page.evaluate("""sample=>{const url=URL.createObjectURL(new Blob([Uint8Array.from(atob(sample),c=>c.charCodeAt(0))],{type:'video/mp4'}));const src=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'src');Object.defineProperty(HTMLMediaElement.prototype,'src',{...src,set(value){src.set.call(this,value.startsWith('/api/recordings/')?url:value)}})}""",base64.b64encode(data).decode())
        js=(ROOT/'app.js').read_text().rstrip();assert js.endswith('start();')
        page.evaluate(js[:-len('start();')]+'''\nme={timezone:'Europe/Warsaw'};cameras=Array.from({length:11},(_,i)=>({id:i+1,name:'Kamera '+(i+1)}));
        $('application').hidden=false;$('camera-view').hidden=true;$('login-panel').hidden=true;$('sync-view').hidden=false;
        $('sync-start').value='2026-11-27T12:00:00';
        for(const camera of cameras){const label=element('label'),input=element('input');input.type='checkbox';input.value=camera.id;input.checked=true;label.append(input,document.createTextNode(camera.name));$('sync-cameras').append(label);}
        api=async url=>url==='/api/time/resolve'?{utc:'2026-11-27T11:00:00Z'}:{recordings:url.includes('/11/recordings')?[]:[{id:Number(url.match(/cameras\\/(\\d+)/)[1]),utc_first:'2026-11-27T11:00:00Z',utc_second:null,duration:15}]};
        window.prepare=prepareSync;''')
        page.evaluate('prepare()');cards=page.locator('#sync-grid .sync-card');assert cards.count()==11
        for card in cards.all():
            video=card.locator('video').bounding_box(); box=card.bounding_box(); header=card.locator('.sync-card-heading').bounding_box()
            assert abs(video['width']/video['height']-16/9)<.03
            assert box['height']-video['height']<=37 and header['height']==34
            assert card.locator('.camera-actions').count()==0
        page.locator('#sync-play').click();page.wait_for_function("document.querySelectorAll('.sync-card-status[data-state=playing]').length===10",timeout=15000)
        assert cards.last.locator('.sync-card-status').inner_text()=='Brak nagrania'
        page.locator('#sync-pause').click();assert cards.first.locator('.sync-card-status').inner_text()=='Pauza'
        assert page.evaluate("Array.from(document.querySelectorAll('#sync-grid video')).every(v=>v.paused)")
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
        assert page.evaluate('document.documentElement.scrollWidth')<=390
        assert cards.first.bounding_box()['width']<=390
        assert not errors,errors
        browser.close();print('Chromium sync archive: 11 cards, compact geometry, real MP4, missing recording, pause and mobile width OK')
