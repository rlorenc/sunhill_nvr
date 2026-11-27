"""Actual Chromium test of the shared per-account frame visibility preference."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static'
js=(root/'app.js').read_text();css=(root/'app.css').read_text()
code=js[js.index('let liveLayoutEditing='):js.index('function liveGridMetrics')]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('SUNHILL_BROWSER_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page()
    page.set_content('<style>'+css+'</style><main id="application"><button id="live-detection-boxes"></button><button id="archive-detection-boxes"></button><select id="layout-select"><option value=""></option></select><div id="live-test" style="position:relative;height:100px"><span class="detection-box" data-kind="person" style="width:30px;height:30px"></span><span class="native-track-dot" style="left:50px;top:50px"></span></div><div id="archive-test" style="position:relative;height:100px"><span class="detection-box" data-kind="motion" style="width:30px;height:30px"></span></div><span class="legend-item" data-kind="motion">Ruch</span></main>')
    page.evaluate('''()=>{window.$=id=>document.getElementById(id);window.me={id:1};window.reconcileLiveLayout=()=>{};window.notify=()=>{};window.saved=null;window.api=async(url,method,payload)=>{if(method==='POST'){window.saved=payload;return {ok:true};}return {workspace:window.saved};};}''')
    page.evaluate(code+'\nwindow.restore=restoreLiveWorkspace;window.flush=flushLiveWorkspace;window.newSession=()=>{workspaceReady=false;return restoreLiveWorkspace();};')
    page.evaluate('restore()')
    assert page.locator('.detection-box:visible').count()==2
    page.locator('#live-detection-boxes').click();page.evaluate('flush()')
    assert page.locator('.detection-box:visible').count()==0
    assert page.locator('.native-track-dot').is_visible() and page.locator('.legend-item').is_visible()
    assert page.evaluate('saved.detection_boxes') is False
    page.evaluate('newSession()')
    assert page.locator('.detection-box:visible').count()==0
    page.locator('#archive-detection-boxes').click();page.evaluate('flush()')
    assert page.locator('.detection-box:visible').count()==2
    assert page.locator('#live-detection-boxes').inner_text()=='Ukryj ramki AI'
    assert page.evaluate('saved.detection_boxes') is True
    browser.close()
    print('Chromium: live/archive frames toggle, stored preference, unchanged trails and legend OK')
