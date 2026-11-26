"""Local Chromium UI regression, with mocked API; no production services/data."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import json

STATIC = Path(__file__).resolve().parents[1] / 'src/sunhill_nvr/static'
ORIGIN = 'http://127.0.0.1:8765'

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'], headless=True)
        try:
            for width, height, role, empty in [(1593,960,'viewer',False),(390,844,'viewer',False),(820,1180,'viewer',False),(390,844,'admin',True)]:
                context=browser.new_context(viewport={'width':width,'height':height})
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                cams=[] if empty else [dict(id=i,slug=f'cam{i}',name=name,enabled=True,sub_configured=True,may_download=True,stream_health='available',health='recording') for i,name in enumerate(['Brama','Parking','Wejście','Ogród'],1)]
                layouts=[{'id':7,'name':'Brama i parking','cameras':[1,2]}] if cams else []
                state={'layout_error':False,'exports':0}
                def route(r):
                    url=r.request.url.removeprefix(ORIGIN);method=r.request.method
                    if url in ('/','/assets/app.css','/assets/app.js'):
                        name={'/':'index.html','/assets/app.css':'app.css','/assets/app.js':'app.js'}[url]
                        r.fulfill(status=200,content_type={'/':'text/html','/assets/app.css':'text/css','/assets/app.js':'application/javascript'}[url],body=(STATIC/name).read_text().replace('__SUNHILL_VERSION__','0.2.14'));return
                    if url.startswith('/live/player/'):
                        r.fulfill(status=200,content_type='application/javascript',body="customElements.define('video-stream', class extends HTMLElement { ondisconnect(){} });");return
                    if url=='/api/me': data={'id':1,'role':role,'username':'test','timezone':'Europe/Warsaw'}
                    elif url=='/api/cameras': data={'cameras':cams}
                    elif url=='/api/layouts':
                        if state['layout_error']:
                            r.fulfill(status=503,json={'error':'Układy niedostępne'});return
                        if method=='POST': layouts.append({'id':8,**r.request.post_data_json})
                        data={'layouts':layouts}
                    elif url=='/api/layouts/7' and method=='DELETE':
                        layouts[:]=[l for l in layouts if l['id']!=7];data={}
                    elif url=='/api/export-jobs':state['exports']+=1;data={'jobs':[]}
                    else:data={}
                    r.fulfill(status=200,content_type='application/json',body=json.dumps(data))
                page.route(ORIGIN+'/**',route)
                page.goto(ORIGIN);expect(page.locator('#application')).to_be_visible();expect(page.locator('#camera-count')).to_have_text(str(len(cams)))
                expect(page.locator('#settings-view')).to_be_hidden();expect(page.locator('#own-password-form')).to_be_hidden();expect(page.locator('#exports-list')).to_be_hidden();expect(page.locator('#layout-form')).to_be_hidden()
                expect(page.locator('#cameras .camera')).to_have_count(len(cams));assert state['exports']==0
                if role=='viewer':expect(page.locator('#show-admin')).to_be_hidden()
                else:expect(page.locator('#show-admin')).to_be_visible()
                def fit():assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'),f'overflow at {width}'
                fit()
                if cams:
                    expect(page.locator('#layout-select option')).to_have_count(2)
                    page.locator('#layout-select').select_option('7');expect(page.locator('#cameras .camera')).to_have_count(2)
                else:expect(page.locator('.empty-state')).to_contain_text('Dodaj kamerę')
                page.locator('#show-settings').click();expect(page.locator('#settings-view')).to_be_visible();expect(page.locator('#own-password-form')).to_be_visible();expect(page.locator('#exports-list')).to_be_visible();expect(page.locator('#layout-form')).to_be_visible();expect(page.locator('#cameras .camera')).to_have_count(0);expect(page.locator('#show-settings')).to_have_attribute('aria-current','page');fit()
                expect(page.locator('#exports-list')).to_contain_text('Brak eksportów')
                if cams:
                    page.locator('#layout-form [name=name]').fill('Nowy układ');page.locator('#layout-form button[type=submit]').click();expect(page.locator('#manage-layout-select option')).to_have_count(3)
                    page.locator('#manage-layout-select').select_option('7');page.once('dialog',lambda d:d.accept());page.locator('#delete-layout').click();expect(page.locator('#manage-layout-select option')).to_have_count(2)
                page.locator('#show-cameras').click();expect(page.locator('#settings-view')).to_be_hidden();expect(page.locator('#cameras .camera')).to_have_count(len(cams));fit()
                if cams:
                    state['layout_error']=True;page.locator('#refresh-cameras').click();expect(page.locator('#notice')).to_contain_text('Układy niedostępne');expect(page.locator('#cameras .camera')).to_have_count(len(cams))
                assert not errors,errors
                print(f'Chromium {width}x{height} {role}: OK',flush=True);context.close()
        finally:browser.close()

if __name__=='__main__':run()
