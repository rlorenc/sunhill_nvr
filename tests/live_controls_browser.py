"""Chromium test of the real live renderer/CSS with synthetic go2rtc elements."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static'
js=(root/'app.js').read_text();css=(root/'app.css').read_text()
layout=js[js.index('let liveLayoutEditing='):js.index('function livePlayerReady')]
health=js[js.index('function cameraHealthText'):js.index('function view(')]
renderer=js[js.index('function liveDiagnostics'):js.index('async function openArchive')]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('SUNHILL_BROWSER_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1593,'height':960})
    page.set_content('<style>'+css+'</style><main id="camera-view"><button id="edit-live-layout">Edytuj</button><button id="reset-live-layout" hidden>Reset</button><p id="live-layout-help" hidden>Edytuj</p><select id="layout-select"></select><span id="camera-count"></span><div id="cameras" class="grid"></div></main>')
    page.evaluate('''() => {window.me={role:'admin'};window.cameras=Array.from({length:11},(_,i)=>({id:i+1,name:'Kamera '+(i+1),slug:'cam'+(i+1),enabled:true,sub_configured:true,health:'recording',stream_health:'available'}));window.layouts=[];window.cameraListState='ready';window.livePreferences=new Map();window.$=id=>document.getElementById(id);window.element=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};window.ensureLivePlayer=()=>{};window.livePlayerReady=()=>true;window.disconnects=0;window.openArchive=c=>{window.archived=c.id;};window.notify=()=>{};window.controlCalls=[];window.controlState={light:false,zoom:{value:3,min:0,max:28}};window.api=async(url,method='GET',data=null)=>{window.controlCalls.push({url,method,data});if(method==='POST'){if(data.control==='light')window.controlState.light=data.value;if(data.control==='zoom')window.controlState.zoom.value=data.value;return {ok:true};}return {controls:window.controlState,settings:url.endsWith('/settings')?[{key:'motion_detection',type:'bool',value:true,label:'Detekcja ruchu',group:'Detekcja'}]:[],partial:false,device:{manufacturer:'Reolink',model:'Lumus',protocol:'Baichuan TCP 9000'}};};customElements.define('video-stream',class extends HTMLElement{connectedCallback(){if(!this.video){this.video=document.createElement('video');this.video.controls=true;this.video.play=()=>Promise.resolve();this.append(this.video);const info=document.createElement('div');info.className='info';info.innerHTML='<div class="status"></div><div class="mode">MSE</div>';this.append(info);this.video.dispatchEvent(new Event('loadedmetadata'));}}disconnectedCallback(){window.disconnects++;}});}''')
    # Playwright's about:blank has an opaque origin; substitute only the test URL base.
    page.evaluate(layout+health+renderer.replace('location.origin','"https://nvr.test"')+'\nrenderCameras();window.testCustomLayout=()=>{liveLayoutSizes.set("1",{span:5,height:260});moveLiveCamera("1","2");applyLiveLayout();};window.testResetLayout=resetLiveLayout;')
    first=page.locator('[data-camera-id="1"]');second=page.locator('[data-camera-id="2"]');menu=first.locator('.live-options')
    assert page.locator('#cameras>.camera').count()==11
    assert page.locator('#cameras .live-options:visible').count()==0
    assert page.locator('#cameras video[controls]').count()==0
    assert page.locator('#cameras video-stream .info .mode').count()==11
    assert page.locator('#cameras video-stream .info .mode:visible').count()==0
    page.evaluate('document.querySelector("video-stream .info .status").textContent="Test błędu"')
    assert first.locator('video-stream .info .status').is_visible()
    page.evaluate('document.querySelector("video-stream .info .status").textContent=""')
    page.evaluate('document.querySelector("video-stream .info .mode").textContent="RTC"')
    assert first.locator('video-stream .info .mode').is_hidden()
    page.evaluate('document.querySelector("video-stream").video.controls=true')
    page.wait_for_function('!document.querySelector("video-stream").video.controls')
    b=first.bounding_box();v=first.locator('video-stream').bounding_box();assert b['height']-v['height']<=40
    # Digital zoom must preserve the existing stream and keep the overlay aligned.
    player=first.locator('video-stream');bzoom=player.bounding_box();cx=bzoom['x']+bzoom['width']/2;cy=bzoom['y']+bzoom['height']/2
    page.mouse.move(cx,cy);page.mouse.wheel(0,-200)
    page.wait_for_function('document.querySelector("video-stream").video.style.transform.includes("scale(1.25)")')
    before=player.locator('video').evaluate('(e)=>e.style.transform');page.mouse.move(cx,cy);page.mouse.down();page.mouse.move(cx+25,cy+15,steps=5);page.mouse.up()
    after=player.locator('video').evaluate('(e)=>e.style.transform');assert before!=after
    assert player.locator('.native-object-overlay').evaluate('(e)=>e.style.transform')==after
    page.mouse.wheel(0,200);page.wait_for_function('!document.querySelector("video-stream").video.style.transform')
    page.wait_for_timeout(550)
    # Real double clicks on video enlarge and restore without reconnecting.
    original=first.bounding_box();page.evaluate('window.originalPlayer=document.querySelector("video-stream")')
    first.locator('video').dblclick();assert first.evaluate('(e)=>e.classList.contains("expanded")') and first.bounding_box()['width']>original['width']
    first.locator('video').dblclick();assert not first.evaluate('(e)=>e.classList.contains("expanded")')
    assert abs(first.bounding_box()['width']-original['width'])<1 and abs(first.bounding_box()['height']-original['height'])<1
    page.evaluate('testCustomLayout()')
    custom=first.bounding_box();order=first.evaluate('(e)=>e.style.order')
    first.locator('video').dblclick();assert first.bounding_box()['width']>custom['width']
    first.locator('video').dblclick();assert abs(first.bounding_box()['width']-custom['width'])<1 and abs(first.bounding_box()['height']-custom['height'])<1 and first.evaluate('(e)=>e.style.order')==order
    assert first.locator('video-stream').evaluate('(e)=>e===originalPlayer && disconnects===0')
    page.evaluate('testResetLayout()')
    first.locator('.live-options-trigger').click();assert menu.is_visible()
    assert first.bounding_box()['height']==b['height']
    page.wait_for_function('controlCalls.length>=11')
    assert menu.locator('.device-controls').count()==0
    assert menu.get_by_role('button',name='Powiększ',exact=True).count()==0
    assert menu.get_by_role('button',name='Zmniejsz',exact=True).count()==0
    menu.get_by_role('button',name='Włącz LED').wait_for()
    assert menu.locator('select').first.locator('option').all_text_contents()==['Niski strumień','Pełna jakość']
    menu.get_by_role('button',name='Włącz LED').click()
    menu.get_by_role('button',name='Wyłącz LED').wait_for()
    assert page.evaluate('controlCalls.some(x=>x.method==="POST"&&x.data.control==="light"&&x.data.value===true)')
    page.evaluate("""() => {const host=document.createElement('div');host.id='manage-cameras';document.body.append(host);const panel=deviceControlPanel(cameras[0]);host.append(panel);panel.open=true;}""")
    panel=page.locator('#manage-cameras .device-controls')
    panel.get_by_role('slider',name='Zoom optyczny').wait_for()
    assert panel.get_by_text('Reolink Lumus',exact=False).count()==1
    assert panel.get_by_role('slider',name='Zoom optyczny').get_attribute('max')=='28'
    assert panel.get_by_text('Ostrość',exact=False).count()==0
    assert panel.get_by_role('button',name='Detekcja ruchu: włączone (zmień)').count()==1
    panel.get_by_role('slider',name='Zoom optyczny').fill('10')
    panel.get_by_role('button',name='Zastosuj').click()
    page.wait_for_function('controlState.zoom.value===10')
    page.evaluate('document.getElementById("manage-cameras").remove()')
    assert first.bounding_box()['height']==b['height']
    if menu.is_hidden():first.locator('.live-options-trigger').click()
    menu.get_by_text('Diagnostyka podglądu',exact=False).click();assert menu.locator('.live-diagnostics').evaluate('(e)=>e.open')
    page.keyboard.press('Escape');assert menu.is_hidden() and first.locator('.live-options-trigger').evaluate('(e)=>e===document.activeElement')
    first.locator('.live-options-trigger').click();second.locator('.live-options-trigger').click();assert menu.is_hidden() and second.locator('.live-options').is_visible()
    page.locator('#edit-live-layout').click();assert second.locator('.live-options').is_hidden()
    page.locator('#edit-live-layout').click()
    first.locator('.live-options-trigger').click();menu.get_by_role('button',name='Włącz dźwięk').click();assert first.locator('video').evaluate('(e)=>!e.muted')
    menu.get_by_role('button',name='Archiwum').click();assert page.evaluate('archived')==1 and menu.is_hidden()
    assert page.evaluate('disconnects')==0
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
    first.locator('.live-options-trigger').click();assert menu.is_visible() and page.evaluate('document.documentElement.scrollWidth')<=390
    browser.close()
    print('Chromium live controls: 11 tiles, controls hidden/restored suppression, compact height, double-click/default and custom layout, menu, diagnostics, escape/outside, audio, archive, mobile OK')
