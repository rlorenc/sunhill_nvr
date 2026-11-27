"""Decode actual fragmented MP4 in Chromium with low-stream MSE policy.
Synthetic source delivers complete four-second GOPs in batches, not a camera.
"""
import base64
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.player_assets import patch_rtc

# The relevant upstream updateend body; same patch as the gateway asset.
update=patch_rtc('''function update(end) {
    const start = end - 5;
    if (this.video.currentTime < start) {
        this.video.currentTime = start;
    }
    const gap = end - this.video.currentTime;
    this.video.playbackRate = gap > 0.1 ? gap : 0.1;
}''')
with tempfile.TemporaryDirectory() as td:
    path=Path(td)/'burst.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10','-t','20','-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-profile:v','baseline','-level','3.0','-g','40','-keyint_min','40','-sc_threshold','0','-movflags','+empty_moov+frag_keyframe+default_base_moof',str(path)],check=True)
    data=path.read_bytes();fragments=[];start=0;pos=0
    while pos<len(data):
        size,kind=struct.unpack_from('>I4s',data,pos)
        assert size>=8 and pos+size<=len(data)
        if kind==b'moof':fragments.append(data[start:pos]);start=pos
        pos+=size
    fragments.append(data[start:pos])
    assert len(fragments)==6
    encoded=[base64.b64encode(fragment).decode() for fragment in fragments]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('SUNHILL_BROWSER_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page()
    page.set_content('<video id="v" muted playsinline></video>')
    page.evaluate('()=>{'+update+'''
window.startBurst=async(parts)=>{
  const video=document.querySelector('video');video.muted=true;
  const ms=new MediaSource();video.src=URL.createObjectURL(ms);
  const state={video,sunhillBufferTarget:4.5,play(){video.play().catch(()=>{});}};
  window.state=state;window.seeks=0;video.addEventListener('seeking',()=>window.seeks++);
  await new Promise(resolve=>ms.addEventListener('sourceopen',resolve,{once:true}));
  const buffer=ms.addSourceBuffer('video/mp4; codecs="avc1.42C01E"');
  window.appendBurst=async(index)=>{
    const chunk=Uint8Array.from(atob(parts[index]),c=>c.charCodeAt(0));
    await new Promise((resolve,reject)=>{buffer.addEventListener('updateend',resolve,{once:true});buffer.addEventListener('error',reject,{once:true});buffer.appendBuffer(chunk);});
    if(video.buffered.length)update.call(state,video.buffered.end(video.buffered.length-1));
  };
  await appendBurst(0);state.play();await appendBurst(1);
};
}''')
    page.evaluate('(parts)=>startBurst(parts)',encoded)
    page.wait_for_function('state.sunhillBufferWaiting && state.video.paused')
    assert page.evaluate('state.video.currentTime')<.2
    page.evaluate('appendBurst(2)');page.wait_for_function('!state.sunhillBufferWaiting && !state.video.paused')
    page.wait_for_function('state.video.currentTime>0.25')
    before=page.evaluate('state.video.currentTime');initial_seeks=page.evaluate('seeks')
    # A new four-second GOP arrives while playback continues. Sample around
    # the four-second boundary: there must not be a discontinuous seek.
    page.wait_for_timeout(3900);page.evaluate('appendBurst(3)')
    page.wait_for_timeout(1100);after=page.evaluate('state.video.currentTime')
    assert 4.4<after-before<5.9,(before,after)
    assert page.evaluate('seeks')==initial_seeks
    assert page.evaluate('state.video.getVideoPlaybackQuality().totalVideoFrames')>=40
    assert page.evaluate('state.video.playbackRate')==1
    browser.close()
print('Chromium MSE: real H.264 four-second fragments, contiguous buffer, continuous clock and decoded frames OK')
