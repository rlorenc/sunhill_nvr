"""Chromium archive playback using two real synthetic MP4s, not production data."""
import base64, subprocess, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static'
with tempfile.TemporaryDirectory() as directory:
    samples=[]
    for color in ('red','blue'):
        path=Path(directory)/(color+'.mp4')
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=160x90:r=10','-t','4','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],check=True)
        samples.append(base64.b64encode(path.read_bytes()).decode())
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1280,'height':800});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        # No network: browser decodes actual media from local Blob URLs.
        html=(ROOT/'index.html').read_text();html=html.replace('<script src="/assets/app.js" defer></script>','')
        import re
        html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
        page.set_content(html);page.add_style_tag(content=(ROOT/'app.css').read_text())
        page.evaluate('(samples)=>{window.sampleURLs=samples.map(s=>URL.createObjectURL(new Blob([Uint8Array.from(atob(s),c=>c.charCodeAt(0))],{type:"video/mp4"})));}',samples)
        js=(ROOT/'app.js').read_text();assert js.rstrip().endswith('start();')
        js=js.rstrip()[:-len('start();')]
        page.evaluate(js+'''\nme={timezone:'Europe/Warsaw'};activeCamera={id:1,name:'Test',may_download:true};
        $('application').hidden=false;$('archive-view').hidden=false;$('archive-date').value='2026-11-25';
        records=[{id:1,utc_first:'2026-11-25T12:00:00Z',utc_second:null,duration:4},{id:2,utc_first:'2026-11-25T12:00:04Z',utc_second:null,duration:4}];
        archiveSource=id=>sampleURLs[id-1];
        window.archiveTest={play:()=>playRecord(0,1,true,true),warm:warmArchiveNeighbours,seek:()=>playRecord(1,1,false,true),back:()=>playRecord(0,2,false,true),id:()=>archiveRecordId,count:()=>archiveBuffers.size,clear:clearArchiveBuffers,pending:()=>Boolean(archiveSwitch)};
        window.runArchiveTest=code=>eval(code);archiveTest.play();''')
        page.wait_for_function('document.querySelector("#archive-player").readyState>=2 && Math.abs(document.querySelector("#archive-player").currentTime-1)<0.15')
        page.evaluate('archiveTest.warm()');page.wait_for_function('archiveTest.count()===1 && document.querySelector(".archive-standby").readyState>=2')
        page.evaluate('window.oldVideo=document.querySelector("#archive-player");archiveTest.seek()')
        page.wait_for_function('archiveTest.id()===2 && !archiveTest.pending() && Math.abs(document.querySelector("#archive-player").currentTime-1)<0.15')
        assert page.evaluate('oldVideo!==document.querySelector("#archive-player") && document.querySelector("#archive-player").paused')
        page.locator('#archive-fullscreen').click();page.wait_for_function('document.fullscreenElement?.id==="archive-stage"')
        assert page.locator('#archive-player').bounding_box()['height']>=795
        page.evaluate('archiveTest.back()');page.wait_for_function('archiveTest.id()===1 && !archiveTest.pending() && Math.abs(document.querySelector("#archive-player").currentTime-2)<0.15')
        assert page.evaluate('document.fullscreenElement?.id==="archive-stage"')
        page.locator('#archive-exit-fullscreen').click();page.wait_for_function('!document.fullscreenElement')
        page.evaluate('archiveTest.clear()');assert page.evaluate('archiveTest.count()===0 && document.querySelectorAll("#archive-stage video").length===1')
        # Calendar renders a real month grid, disables empty days, and highlights indexed days.
        page.evaluate("code=>runArchiveTest(code)","$('archive-date').value='2026-10-23';syncArchiveDateLabel();renderCalendar('2026-10',['2026-10-02','2026-10-23']);$('archive-calendar-panel').open=true;api=async()=>({days:['2026-10-02','2026-10-23']});$('archive-month').value='2026-10'")
        page.wait_for_function("document.querySelectorAll('#calendar-days button.available').length===2")
        assert page.locator('#calendar-days button').count()==31
        assert page.locator('#calendar-days button:disabled').count()==29
        assert page.locator('#calendar-days button[aria-current=date]').inner_text()=='23'
        # Bad newest segment: actual HTML5 decoder fails, then previous real MP4 plays.
        page.evaluate("code=>runArchiveTest(code)","""$('archive-calendar-panel').open=false;playbackGeneration++;clearArchiveBuffers();
        const video=$('archive-player');video.pause();video.removeAttribute('src');video.load();
        const day=cameraDay();$('archive-date').value=day;
        const good={id:1,local_time:day+'T10:00:00',utc_first:day+'T09:00:00Z',utc_second:null,duration:4};
        const bad={id:2,local_time:day+'T10:00:04',utc_first:day+'T09:00:04Z',utc_second:null,duration:4};
        window.badURL=URL.createObjectURL(new Blob(['not a valid mp4'],{type:'video/mp4'}));
        archiveSource=id=>id===2?badURL:sampleURLs[0];
        api=async url=>url.includes('media-info')?{streams:[{type:'video',codec:'hevc',tag:'hev1'}]}:url.includes('/recordings?')?{recordings:[good,bad],next_cursor:null}:{events:[],next_cursor:null};
        fetch=async()=>({ok:true,status:200,headers:{get:()=> 'video/mp4'}});
        archiveRecordId=null;loadedArchiveDate=null;loadArchive(true);""")
        page.wait_for_function("document.querySelector('#archive-player').readyState>=2 && !document.querySelector('#archive-player').paused && document.querySelector('#archive-playback-note').textContent.includes('Nagranie #2')", timeout=10000)
        assert page.evaluate('archiveTest.id()===1')
        assert 'hevc' in page.locator('#archive-playback-note').inner_text()
        assert not errors,errors
        browser.close()
        print('Chromium: real MP4 decoding, adjacent prefetch, forward/back seek while paused, full-screen video stage remains active, cleanup OK')
