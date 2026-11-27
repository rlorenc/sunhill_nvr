"""Bounded on-demand archive motion search. No frame or metadata files are created.

The decoder samples two grayscale frames per second at 160x90. A motion hit
means at least 5% of pixels in the selected ROI changed by 24 intensity levels.
This is image differencing, not AI classification or a guarantee of no motion.
"""
from __future__ import annotations
import math
import subprocess
from .camera_events import normalize_box
from .media import _source_argument, MediaError

WIDTH, HEIGHT, FPS = 160, 90, 2
MAX_SECONDS = 120


def scan(fd, start, duration, box, *, timeout=20, ffmpeg='ffmpeg'):
    normalize_box(box)
    if (not math.isfinite(start) or not math.isfinite(duration) or start < 0
            or not 1 <= duration <= MAX_SECONDS or timeout <= 0):
        raise ValueError('invalid motion search range')
    x, y, w, h = box
    left, top = math.floor(x * WIDTH), math.floor(y * HEIGHT)
    right, bottom = min(WIDTH, math.ceil((x+w)*WIDTH)), min(HEIGHT, math.ceil((y+h)*HEIGHT))
    if right-left < 3 or bottom-top < 3:
        raise ValueError('motion search area too small')
    count = math.ceil(duration * FPS)
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '1',
            '-filter_threads', '1', '-ss', str(start), '-t', str(duration),
            '-protocol_whitelist', 'file,pipe', '-i', _source_argument(fd), '-map', '0:v:0',
            '-an', '-sn', '-dn', '-vf', f'fps={FPS},scale={WIDTH}:{HEIGHT},format=gray',
            '-frames:v', str(count), '-f', 'rawvideo', 'pipe:1']
    try:
        proc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=True, pass_fds=(fd,))
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError('motion scan failed') from exc
    raw = proc.stdout; stride = WIDTH * HEIGHT
    if len(raw) % stride or not stride <= len(raw) <= count * stride:
        raise MediaError('incomplete motion samples')
    frames = len(raw)//stride
    # Do not treat missing frames as a negative result.
    if frames < max(2, math.floor(duration*FPS)-1):
        raise MediaError('incomplete motion samples')
    roi_size=(right-left)*(bottom-top); minimum=max(1, math.ceil(roi_size*.05))
    hits=[]
    for i in range(1,frames):
        changed=0; a=(i-1)*stride; b=i*stride
        for row in range(top,bottom):
            lo=row*WIDTH+left;hi=row*WIDTH+right
            changed+=sum(abs(v-u)>=24 for u,v in zip(raw[a+lo:a+hi],raw[b+lo:b+hi]))
        if changed>=minimum:
            moment=start+i/FPS
            if hits and moment-hits[-1]['end']<=1/FPS:
                hits[-1]['end']=moment
            else:hits.append({'start':moment,'end':moment})
    return {'hits':hits,'sample_count':frames,'sample_fps':FPS}
