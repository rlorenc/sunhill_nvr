"""Optional real Chromium UI test with a mock HTTP API, no production data."""
import json, os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static'

def main():
    cameras=[dict(id=i,slug=f'cam-{i}',name=name,enabled=True,recording=mode,accounts=[],may_download=True,sub_configured=True,onvif=dict(url='',username='',password_configured=False,auth_mode='digest',checked=None,offset=None,error=None,fresh=False)) for i,name,mode in [(1,'Brama','ftp'),(2,'Parking','rtsp')]]
    accounts=[dict(id=1,username='brama-ftp',enabled=True,camera_id=1),dict(id=2,username='shared',enabled=True,camera_id=None)]
    created=[]
    api={'/api/me':dict(username='admin',role='admin',timezone='Europe/Warsaw'),'/api/cameras':dict(cameras=cameras),'/api/layouts':dict(layouts=[]),'/api/admin/inventory':dict(users=[],accounts=accounts,cameras=cameras),'/api/settings/home-assistant':dict(url='',enabled=False),'/api/settings/storage':dict(archive='/home/store/kamery',exports='/exports',staging='/staging',minimum_free=10**10,free_buffer=5*10**9),'/api/settings/monitoring':dict(stale_after=900),'/api/settings/recording':dict(segment_seconds=300),'/api/audit':dict(events=[]),'/api/settings/domain':dict(origin='https://nvr.test',supported=True,pending=False,status=dict(state='idle')),'/api/settings/tls':dict(pending=False,status={}),'/api/rotation':dict(history=[]),'/api/system/health':dict(services=[],system=[]),'/api/updates':dict(version='0.3.2',pending=False,status=dict(state='idle'),packages=[]),'/api/export-jobs':dict(jobs=[])}
    def route(r):
        path=urlsplit(r.request.url).path
        if path=='/':return r.fulfill(content_type='text/html',body=(ROOT/'index.html').read_text().replace('__SUNHILL_VERSION__','0.3.2'))
        if path in ['/assets/app.js','/assets/app.css']:return r.fulfill(content_type='text/javascript' if path.endswith('.js') else 'text/css',body=(ROOT/path.split('/')[-1]).read_text())
        if path.endswith('/video-stream.js'):return r.fulfill(content_type='text/javascript',body="customElements.define('video-stream',class extends HTMLElement{});")
        if path=='/api/ftp-accounts' and r.request.method=='POST':
            data=r.request.post_data_json;created.append(data);return r.fulfill(status=201,json={'id':3})
        if path in api:return r.fulfill(json=api[path])
        for suffix,key in [('/recordings','recordings'),('/events','events'),('/calendar','days')]:
            if path.endswith(suffix):return r.fulfill(json={key:[]})
        return r.fulfill(status=404,json={'error':'fixture endpoint missing'})
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox'])
        for w,h in [(1440,900),(390,844)]:
            page=browser.new_page(viewport={'width':w,'height':h});errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.route('**/*',route);page.goto('http://127.0.0.1:8765/')
            expect(page.locator('#cameras .camera')).to_have_count(2)
            page.locator('#show-admin').click();expect(page.locator('#overview-cameras')).to_have_text('2 / 2');expect(page.locator('#camera-form')).to_be_hidden()
            page.locator('#admin-tab-cameras').click();expect(page.locator('#manage-cameras .camera-config')).to_have_count(2)
            page.locator('#camera-admin-search').fill('brama');expect(page.locator('#manage-cameras .camera-config:visible')).to_have_count(1);page.locator('#camera-admin-search').fill('')
            card=page.locator('.camera-config[data-camera-id="1"]');card.locator(':scope > summary').click();expect(card.get_by_text('brama-ftp',exact=False).first).to_be_visible();expect(page.locator('#ftp-accounts')).not_to_contain_text('brama-ftp')
            card.get_by_text('Dodaj indywidualne konto FTP',exact=True).click();card.get_by_role('button',name='Wygeneruj hasło').click();card.get_by_role('button',name='Utwórz konto dla tej kamery').click();expect(card.get_by_text('Konto utworzone.',exact=False).first).to_be_visible();assert created[-1]['camera_id']==1 and len(created[-1]['password'])==36
            page.locator('#admin-tab-storage').click();expect(page.locator('#archive-form')).to_be_visible();expect(card).to_be_hidden()
            page.locator('#show-archive').click();expect(page.locator('#archive-player')).to_be_visible();expect(page.locator('#recordings')).to_have_count(0);expect(page.locator('#archive-player')).not_to_have_attribute('controls','');page.locator('#archive-camera-select').select_option('2');expect(page.locator('#archive-title')).to_have_text('Archiwum - Parking');page.locator('#archive-sync').click();expect(page.locator('#sync-view')).to_be_visible();page.locator('#sync-back-archive').click();expect(page.locator('#archive-view')).to_be_visible();assert not errors,errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'),f'overflow {w}'
            print(f'UI fixture {w}x{h}: OK');page.close()
        browser.close()
if __name__=='__main__':main()
