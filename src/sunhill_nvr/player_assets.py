"""Serve only the two local go2rtc player assets, with a narrow MSE patch.

The gateway is already a trusted script source via nginx. Do not proxy arbitrary
URLs, redirects, credentials, or gateway API calls through this module.
"""
import http.client
import re

MAX_SCRIPT_BYTES = 256 * 1024
ASSET_PATH = re.compile(r'/assets/live-player/[1-9][0-9]{0,11}/(video-stream|video-rtc)\.js\Z')
_TRIM = 'const start = end - 5;'
_SYNC = re.compile(r'if\s*\(this\.video\.currentTime\s*<\s*start\)\s*\{\s*'
                   r'this\.video\.currentTime\s*=\s*start;\s*\}\s*'
                   r'const gap = end - this\.video\.currentTime;\s*'
                   r'this\.video\.playbackRate = gap > 0\.1 \? gap : 0\.1;')
# Keep the upstream append queue, eviction, codec negotiation, reconnect and
# WebSocket protocol. A common media clock keeps audio and video aligned.
_SYNC_REPLACEMENT = '''{ // Sunhill NVR: smooth live playback (seconds).
                    const video = this.video;
                    const ranges = video.buffered;
                    const last = ranges.length - 1;
                    if (last >= 0) {
                        const liveEnd = ranges.end(last);
                        const liveStart = Math.max(start, ranges.start(last));
                        // Only seek after eviction or a long discontinuity.
                        // Never seek into a hole between buffered ranges.
                        let inRange = false;
                        for (let i = 0; i < ranges.length; i++) {
                            if (video.currentTime >= ranges.start(i) && video.currentTime < ranges.end(i)) inRange = true;
                        }
                        const stalledInHole = !inRange && video.readyState < 3 &&
                            video.currentTime < liveStart && liveEnd - liveStart >= 2.5;
                        if (!video.seeking && liveEnd > liveStart && (video.currentTime < start || stalledInHole)) {
                            video.currentTime = Math.max(liveStart, liveEnd - 2.5);
                        }
                        const ahead = liveEnd - video.currentTime;
                        const rate = ahead < 2 ? 0.95 : ahead > 3 ? 1.05 : 1.0;
                        if (video.playbackRate !== rate) video.playbackRate = rate;
                    }
                    }'''
_IMPORT = re.compile(r'''(['"])(?:\./|/)?video-rtc\.js(?:\?[^'"\r\n]*)?\1''')


def patch_rtc(source: str) -> str:
    # Unknown future upstream: preserve playback, explicitly report native mode.
    # Apply both changes or neither. Match the exact observed 1.9.15 algorithm.
    if (source.count(_TRIM) != 1 or len(_SYNC.findall(source)) != 1
            or source.index(_TRIM) > _SYNC.search(source).start()):
        return source + '\n;globalThis.sunhillMsePolicy = "native";\n'
    source = source.replace(_TRIM, 'const start = end - 10;', 1)
    source = _SYNC.sub(lambda _: _SYNC_REPLACEMENT, source, count=1)
    return source + '\n;globalThis.sunhillMsePolicy = "smooth";\n'


def load_player_asset(name: str) -> bytes:
    if name not in ('video-stream', 'video-rtc'):
        raise ValueError('unknown player asset')
    connection = http.client.HTTPConnection('127.0.0.1', 1984, timeout=3)
    try:
        connection.request('GET', '/' + name + '.js', headers={'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise ValueError('gateway asset unavailable')
        data = response.read(MAX_SCRIPT_BYTES + 1)
        if not data or len(data) > MAX_SCRIPT_BYTES:
            raise ValueError('invalid gateway asset size')
        source = data.decode('utf-8')
        if name == 'video-rtc':
            return patch_rtc(source).encode('utf-8')
        # Force the dependency to stay in the same retry-specific URL namespace.
        if len(_IMPORT.findall(source)) != 1:
            raise ValueError('unsupported player import')
        return _IMPORT.sub("'./video-rtc.js'", source, count=1).encode('utf-8')
    finally:
        connection.close()
