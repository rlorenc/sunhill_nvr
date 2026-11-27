"""Actual Chromium, complete application with a go2rtc lifecycle fixture.
The fixture does not decode camera video; see test_player_assets for MSE policy.
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static'
html=(root/'index.html').read_text();css=(root/'app.css').read_text()
js=(root/'app.js').read_text().replace('location.origin','"https://nvr.test"')+'''
window.testRecover=recoverLivePlayers;
window.testStatus=refreshCameraStatus;
'''
player='''
window.connections=0;window.disconnects=0;window.badAutoplay=0;
customElements.define('video-stream',class extends HTMLElement {
  set src(url){this.wsURL=String(url);}
  oninit(){this.video=document.createElement('video');this.video.controls=true;
    this.video.play=()=>Promise.resolve();let t=0;Object.defineProperty(this.video,'currentTime',{get:()=>t,set:v=>{t=v;}});
    // Upstream video-stream discards any existing children.
    this.innerHTML='<div class="info"><div class="status"></div></div>';this.prepend(this.video);
  }
  connectedCallback(){if(!this.video)this.oninit();if(!this.video.muted)window.badAutoplay++;window.connections++;}
  ondisconnect(){window.disconnects++;}
});
'''
cameras=[dict(id=i,slug=f'cam{i}',name=f'Kamera {i}',enabled=True,sub_configured=True,health='recording',stream_health='available') for i in range(1,12)]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('SUNHILL_BROWSER_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    def fresh_page():
        page=browser.new_page(viewport={'width':1590,'height':960})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        local_html=re.sub(r'<link[^>]*href="/assets/app.css"[^>]*>','<style>'+css+'</style>',html)
        local_html=re.sub(r'<script[^>]*src="/assets/app.js"[^>]*></script>','',local_html)
        page.set_content(local_html)
        page.evaluate('''(data)=>{window.testCameras=data;}''',cameras)
        # Replace network access with delayed promises, not the application
        # lifecycle. Simulate the asset registration before workspace returns.
        fixture="""
const originalAppend=document.head.append.bind(document.head);
document.head.append=function(item){if(item.tagName==='SCRIPT'&&String(item.src).includes('/video-stream.js')){setTimeout(()=>{PLAYER_SOURCE;item.onload();},30);}else originalAppend(item);};
api=async(url,method,data)=>{
  if(url==='/api/me')return {id:1,username:'admin',role:'admin',timezone:'Europe/Warsaw'};
  if(url==='/api/cameras')return {cameras:window.testCameras};
  if(url==='/api/layouts')return {layouts:[{id:1,name:'Test',cameras:window.testCameras.map(c=>c.id)}]};
  if(url==='/api/live-workspace'){await new Promise(resolve=>setTimeout(resolve,250));return {workspace:{layout_id:1,order:window.testCameras.map(c=>c.id).reverse(),sizes:{},detection_boxes:true}};}
  if(url.endsWith('/controls'))return {controls:{},talkback:false};
  if(url==='/api/metadata/live')return {cameras:{},motion:{}};
  return {};
};
start();
""".replace('PLAYER_SOURCE',player)
        page.evaluate('''()=>{window.testIntervals=[];const original=window.setInterval;window.setInterval=(callback,ms,...args)=>{window.testIntervals.push({callback,ms});return original(callback,ms,...args);};}''')
        page.evaluate(js.replace('\nstart();','\n'+fixture,1))
        return page,errors
    page,errors=fresh_page()
    page.wait_for_function('document.querySelectorAll("#cameras video-stream").length===11')
    page.wait_for_timeout(500)
    assert page.evaluate('connections===11 && disconnects===0 && badAutoplay===0')
    assert page.locator('video-stream .native-object-overlay').count()==11
    assert page.locator('#layout-select').input_value()=='1'
    first=page.locator('.camera[data-camera-id="1"]')
    assert first.locator('video-stream').evaluate('(e)=>e.sunhillBufferTarget===4.5 && e.wsURL.endsWith("cam1_sub")')
    first.locator('video-stream').dblclick();first.locator('video-stream').dblclick()
    assert page.evaluate('connections===11 && disconnects===0')
    page.evaluate('testStatus()');assert page.evaluate('connections===11')
    # Simulate both periodic refresh cycles; the badge must never expand.
    page.evaluate('''async()=>{for(const timer of testIntervals.filter(t=>t.ms===30000||t.ms===60000))await timer.callback();}''')
    assert all(value=='REC' for value in page.locator('#cameras .health').all_text_contents())
    assert first.locator('.health').get_attribute('title')=='Obraz: dostępny | Zapis: nagrywa'
    # A single stuck stream is recovered, with no cycling of healthy tiles.
    page.evaluate('''()=>{let now=Date.now();Date.now=()=>now;testRecover();now+=21000;for(const card of document.querySelectorAll('.camera'))if(card.dataset.cameraId!=='1')card.querySelector('video').currentTime=21;testRecover();Date.now=()=>now+1000;testRecover();}''')
    assert page.evaluate('connections===12 && disconnects===1 && badAutoplay===0')
    first.locator('.live-options-trigger').click();first.get_by_role('combobox',name='Jakość podglądu Kamera 1').select_option('main')
    assert first.locator('video-stream').evaluate('(e)=>e.sunhillBufferTarget===0 && e.wsURL.endsWith("cam1")')
    page.locator('#refresh-cameras').click();page.wait_for_function('connections===24')
    page.wait_for_timeout(350);assert page.evaluate('connections===24')
    assert not errors,errors
    page.close();page,errors=fresh_page();page.wait_for_function('document.querySelectorAll("#cameras video-stream").length===11');page.wait_for_timeout(350)
    assert page.evaluate('connections===11 && disconnects===0 && badAutoplay===0')
    assert not errors,errors
    browser.close()
print('Chromium live startup: 11 connections once, restored layout, muted before connect, overlay, resize, targeted recovery, refresh/reload OK')
