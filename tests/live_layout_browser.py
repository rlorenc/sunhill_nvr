"""Optional Chromium fixture (requires Playwright and /usr/bin/chromium).
Exercises actual layout CSS, pointer events and custom-element lifecycle with
synthetic player elements, not real camera streams or backend authentication.
Run: python tests/live_layout_browser.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static'
js=(root/'app.js').read_text(); code=js[js.index('let liveLayoutEditing='):js.index('function selectedLiveAlias')]
css=(root/'app.css').read_text()
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1440,'height':1100})
    page.set_content('<style>'+css+'</style><main><button id="edit-live-layout">Edytuj widok</button><button id="reset-live-layout" hidden>Reset</button><p id="live-layout-help" hidden>Edytuj</p><div id="cameras" class="grid" data-count="3"></div></main>')
    page.evaluate('''() => {window.cameras=[1,2,3].map(id=>({id,name:'Camera '+id}));window.$=id=>document.getElementById(id);window.element=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};window.disconnects=0;customElements.define('video-stream',class extends HTMLElement{disconnectedCallback(){window.disconnects++;}});}''')
    page.evaluate(code+'''\nfor(const camera of cameras){const card=element('article',null,'camera');card.dataset.cameraId=String(camera.id);card.append(element('div',camera.name,'camera-heading'),element('video-stream'),element('div','controls','camera-actions'));installLiveLayoutHandles(card,camera);$('cameras').append(card);}reconcileLiveLayout();applyLiveLayout();''')
    page.locator('#edit-live-layout').click()
    first=page.locator('[data-camera-id="1"]');third=page.locator('[data-camera-id="3"]')
    def center(loc):
        b=loc.bounding_box();return b['x']+b['width']/2,b['y']+b['height']/2
    before=first.bounding_box()['width'];before_h=first.locator('video-stream').bounding_box()['height']
    a=center(first.locator('.live-resize-handle'));page.mouse.move(*a);page.mouse.down();page.mouse.move(a[0]+120,a[1]+85,steps=12);page.mouse.up()
    assert first.bounding_box()['width']>before and first.locator('video-stream').bounding_box()['height']>before_h
    page.locator('#reset-live-layout').click()
    a=center(first.locator('.live-move-handle'));b=center(third.locator('video-stream'))
    page.mouse.move(*a);page.mouse.down();page.mouse.move(*b,steps=12);page.mouse.up()
    assert int(first.evaluate('(el)=>getComputedStyle(el).order'))==2
    width=first.bounding_box()['width'];height=first.locator('video-stream').bounding_box()['height'];a=center(first.locator('.live-resize-handle'))
    # Keyboard works when the last column lacks physical drag room.
    first.locator('.live-resize-handle').focus();page.keyboard.press('ArrowRight');page.keyboard.press('ArrowDown')
    assert first.bounding_box()['width']>width and first.locator('video-stream').bounding_box()['height']>height
    page.locator('#edit-live-layout').click();assert first.locator('.live-layout-handles').is_hidden()
    assert page.evaluate('disconnects')==0
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
    assert first.bounding_box()['width']<=390 and page.evaluate('document.documentElement.scrollWidth')<=390
    page.locator('#reset-live-layout').click();assert int(first.evaluate('(el)=>getComputedStyle(el).order'))==0
    browser.close();print('Chromium fixture: pointer drag, pointer/keyboard resize, reset, mobile width, no DOM disconnect OK')
