import json
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch
from sunhill_nvr import player_assets as assets

# The exact synchronization lines reported from the installed go2rtc 1.9.15,
# embedded in a minimal JS method, not a copy of the complete upstream player.
FIXTURE = '''function update(end) {
    const start = end - 5;
    // Other upstream operations remain intact.
    if (this.video.currentTime < start) {
        this.video.currentTime = start;
    }
    const gap = end - this.video.currentTime;
    this.video.playbackRate = gap > 0.1 ? gap : 0.1;
    return start;
}'''

class PlayerAssetTests(unittest.TestCase):
    def test_exact_patch_or_native_fallback(self):
        result = assets.patch_rtc(FIXTURE)
        self.assertIn('end - 10', result)
        self.assertNotIn('gap > 0.1', result)
        self.assertIn('Other upstream operations remain intact', result)
        self.assertIn('sunhillMsePolicy = "smooth"', result)
        for unknown in (FIXTURE.replace('end - 5', 'end - 6'), FIXTURE.replace('gap > 0.1', 'gap > 0.2'), FIXTURE*2):
            self.assertEqual(assets.patch_rtc(unknown), unknown+'\n;globalThis.sunhillMsePolicy = "native";\n')

    @unittest.skipUnless(shutil.which('node'), 'Node required for JS execution')
    def test_transformed_js_playback_policy(self):
        script = assets.patch_rtc(FIXTURE) + '''
function run(time, ranges, readyState=4, seeking=false) {
    const video = {currentTime:time, playbackRate:1, readyState, seeking,
      buffered:{length:ranges.length,start:i=>ranges[i][0],end:i=>ranges[i][1]}};
    const trim=update.call({video},20);
    return [video.currentTime,video.playbackRate,trim];
}
console.log(JSON.stringify([
    run(19.9,[[10,20]]),run(17.5,[[10,20]]),run(16,[[10,20]]),
    run(5,[[10,20]]),run(13,[[10,12],[15,20]],2),
    run(11,[[10,12],[15,20]],2),run(13,[[10,12],[15,16]],2),
    run(13,[[10,12],[15,20]],2,true),run(17,[]),
    globalThis.sunhillMsePolicy
]));
'''
        result = subprocess.run(['node','-'],input=script,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(result.stdout), [[19.9,.95,10],[17.5,1,10],[16,1.05,10],[17.5,1,10],[17.5,1,10],[11,1.05,10],[13,1,10],[13,1.05,10],[17,1,10],'smooth'])

    def test_loopback_only_and_dependency_import(self):
        response = MagicMock(status=200)
        response.getheader.return_value='identity'
        response.read.return_value=b"import {VideoRTC} from './video-rtc.js?v=1.9.15';"
        with patch('sunhill_nvr.player_assets.http.client.HTTPConnection') as factory:
            client=factory.return_value;client.getresponse.return_value=response
            self.assertEqual(assets.load_player_asset('video-stream'), b"import {VideoRTC} from './video-rtc.js';")
            factory.assert_called_once_with('127.0.0.1',1984,timeout=3)
            client.request.assert_called_once_with('GET','/video-stream.js',headers={'Accept-Encoding':'identity'})
            response.read.assert_called_once_with(assets.MAX_SCRIPT_BYTES+1)
            client.close.assert_called_once()
            with self.assertRaises(ValueError):assets.load_player_asset('../api/streams')
            self.assertEqual(factory.call_count,1)

    def test_bad_gateway_responses_rejected(self):
        for status, encoding, data in ((302,'identity',b'js'),(200,'gzip',b'js'),(200,'identity',b''),(200,'identity',b'x'*(assets.MAX_SCRIPT_BYTES+1)),(200,'identity',b'\xff')):
            with self.subTest(status=status,encoding=encoding,size=len(data)),patch('sunhill_nvr.player_assets.http.client.HTTPConnection') as factory:
                client=factory.return_value;response=client.getresponse.return_value;response.status=status;response.getheader.return_value=encoding;response.read.return_value=data
                with self.assertRaises(ValueError):assets.load_player_asset('video-rtc')
                client.close.assert_called_once()

    def test_unrecognized_import_rejected_for_native_route_retry(self):
        with patch('sunhill_nvr.player_assets.http.client.HTTPConnection') as factory:
            response=factory.return_value.getresponse.return_value;response.status=200;response.getheader.return_value='identity';response.read.return_value=b'import "other.js";'
            with self.assertRaises(ValueError):assets.load_player_asset('video-stream')
