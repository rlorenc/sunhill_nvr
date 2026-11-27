"""Local, bounded visual fallback when native ONVIF metadata is unavailable.

Uses OpenCV's shipped Haar plate/face detectors and Tesseract, without saving
frames. Plate text is published only after two matching readings on one track.
HOG/SVM person and Haar face detection are local. Optional explicitly enrolled face profiles use provisioned YuNet/SFace models. No model downloads or external API.
"""
from __future__ import annotations
import re
import subprocess
import threading
import time
from pathlib import Path

from .cameras import SLUG
from .analytics_models import generation as model_generation

WIDTH, HEIGHT = 960, 540
# Bound CPU usage across cameras; native metadata is preferred.
SLOTS = threading.BoundedSemaphore(2)
PLATE = re.compile(r'[A-Z]{1,3}[A-Z0-9]{3,6}\Z')


def read_plate(gray, cv):
    """OCR a detected crop; return None rather than guessing unreadable text."""
    if gray.ndim != 2 or min(gray.shape) < 12 or gray.shape[1] < 35:
        return None
    factor = min(4, max(1, 240 / gray.shape[1]))
    image = cv.resize(gray, None, fx=factor, fy=factor, interpolation=cv.INTER_CUBIC)
    image = cv.copyMakeBorder(image, 12, 12, 12, 12, cv.BORDER_CONSTANT, value=255)
    ok, png = cv.imencode('.png', image)
    if not ok:
        return None
    try:
        proc = subprocess.run(['/usr/bin/tesseract', 'stdin', 'stdout', '-l', 'eng', '--psm', '7', '-c', 'tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', 'tsv'], input=png.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2, check=True)
        words = []
        for line in proc.stdout.decode('utf-8').splitlines()[1:]:
            fields = line.split('\t', 11)
            if len(fields) == 12 and fields[0] == '5' and fields[11].strip():
                confidence = float(fields[10])
                if not 65 <= confidence <= 100:
                    return None
                words.append(fields[11].strip().upper())
        value = ''.join(words)
        return value if PLATE.fullmatch(value) and any(c.isdigit() for c in value) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def sample(slug):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ValueError('invalid camera slug')
    try:
        p = subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-rtsp_transport','tcp','-threads','1','-filter_threads','1','-i',f'rtsp://127.0.0.1:8554/{slug}','-map','0:v:0','-an','-sn','-dn','-vf',f'scale={WIDTH}:{HEIGHT},format=bgr24','-frames:v','1','-f','rawvideo','pipe:1'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
        return p.stdout if len(p.stdout) == WIDTH * HEIGHT * 3 else None
    except (OSError, subprocess.SubprocessError):
        return None


def iou(a, b):
    x,y,w,h=a; u,v,s,t=b
    overlap=max(0,min(x+w,u+s)-max(x,u))*max(0,min(y+h,v+t)-max(y,v))
    union=w*h+s*t-overlap
    return overlap/union if union>0 else 0


class Tracks:
    """Track IDs are local to one uninterrupted fallback session."""
    def __init__(self):
        self.previous = {}; self.counter = 0; self.last = None

    def reset(self):
        self.previous={};self.last=None

    def observe(self, detections, stamp):
        if self.last is None or not 0 < stamp-self.last <= 3000:
            self.previous = {}
        self.last = stamp
        next_tracks = {}; result = []
        for kind, box, text in detections:
            candidates=[(iou(box, old['box']), oid) for oid,old in self.previous.items() if old['kind']==kind and (kind in ('plate','face') or old.get('text')==text) and oid not in next_tracks]
            score,oid=max(candidates,default=(0,None))
            if score < .3:
                self.counter+=1; oid=str(self.counter)
            old=self.previous.get(oid,{})
            repeat=old.get('repeat',0)+1 if text and old.get('text')==text else int(bool(text))
            next_tracks[oid]={'kind':kind,'box':box,'text':text,'repeat':repeat}
            obj={'id':oid,'kind':kind,'box':box}
            if kind=='plate' and text and repeat>=2:obj['plate_number']=text
            elif kind=='face' and text and repeat>=2:obj['face_profile']=text
            elif kind not in ('plate','face') and text:obj['label']=text
            result.append(obj)
        self.previous=next_tracks
        return result


class Detector:
    def __init__(self,template_provider=None):
        import cv2
        import numpy
        self.cv=cv2; self.np=numpy
        cv2.setNumThreads(1)
        roots=[Path(getattr(getattr(cv2,'data',None),'haarcascades','/usr/share/opencv4/haarcascades')),Path('/usr/share/opencv4/haarcascades'),Path('/usr/share/opencv/haarcascades')]
        self.models={}
        for kind,name in [('face','haarcascade_frontalface_default.xml'),('plate','haarcascade_russian_plate_number.xml')]:
            for root in roots:
                if (root/name).is_file():
                    cascade=cv2.CascadeClassifier(str(root/name))
                    if not cascade.empty():self.models[kind]=cascade;break
        self.people=cv2.HOGDescriptor()
        self.people.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        if not self.models:raise RuntimeError('local detector models unavailable')
        from .local_objects import CocoDetector
        try:self.coco=CocoDetector(cv2,numpy)
        except (OSError,ValueError,cv2.error):self.coco=None
        self.template_provider=template_provider;self.recognizer=None
        if template_provider is not None:
            from .face_profiles import Recognizer
            try:self.recognizer=Recognizer(cv2,numpy)
            except (OSError,ValueError,AttributeError,cv2.error):pass

    def detect(self, raw):
        if len(raw)==WIDTH*HEIGHT:
            image=self.np.frombuffer(raw,dtype=self.np.uint8).reshape(HEIGHT,WIDTH)
            color=self.cv.cvtColor(image,self.cv.COLOR_GRAY2BGR)
        elif len(raw)==WIDTH*HEIGHT*3:
            color=self.np.frombuffer(raw,dtype=self.np.uint8).reshape(HEIGHT,WIDTH,3)
            image=self.cv.cvtColor(color,self.cv.COLOR_BGR2GRAY)
        else:raise ValueError('invalid frame')
        enhanced=self.cv.equalizeHist(image)
        result=[]; ocr_budget=4
        # HOG/SVM is a shipped local person detector, not a motion guess.
        # Use a smaller image to bound inference cost; map boxes back to full frame.
        small=self.cv.resize(image,(480,270),interpolation=self.cv.INTER_AREA)
        boxes,weights=self.people.detectMultiScale(small,winStride=(8,8),padding=(8,8),scale=1.1)
        if self.coco is not None:
            try:result.extend(self.coco.detect(color))
            except (ValueError,self.cv.error):self.coco=None
        for (x,y,w,h),weight in zip(boxes,weights):
            x,y,w,h=map(int,(x,y,w,h))
            if float(weight)<.5 or min(x,y)<0 or min(w,h)<=0 or x+w>480 or y+h>270:continue
            box=[x/480,y/270,w/480,h/270]
            if not any(k=='person' and iou(box,b)>.3 for k,b,_ in result):result.append(('person',box,None))
        if self.recognizer is not None:
            from .face_profiles import best_match
            try:
                templates=self.template_provider(self.recognizer.model_id)
                for box,feature in self.recognizer.faces(color):
                    result.append(('face',box,best_match(feature,templates) if feature is not None and templates else None))
            except (OSError,ValueError,self.cv.error):self.recognizer=None
        for kind,model in self.models.items():
            for x,y,w,h in model.detectMultiScale(enhanced,scaleFactor=1.1,minNeighbors=5,minSize=(40,15) if kind=='plate' else (30,30)):
                x,y,w,h=map(int,(x,y,w,h))
                if x<0 or y<0 or w<=0 or h<=0 or x+w>WIDTH or y+h>HEIGHT:continue
                if kind=='plate' and not 2<=w/h<=7:continue
                text=None
                if kind=='plate' and ocr_budget:
                    ocr_budget-=1;text=read_plate(image[y:y+h,x:x+w],self.cv)
                box=[x/WIDTH,y/HEIGHT,w/WIDTH,h/HEIGHT]
                if kind=='face' and any(k=='face' and iou(box,b)>.3 for k,b,_ in result):continue
                result.append((kind,box,text))
        return [item for item in result if item[0]!='package']


def fallback(slug, stop, emit, *, duration=900, factory=Detector, sampler=sample, generation=model_generation):
    """Run only between native attempts, without blocking other camera sessions."""
    initial_generation=generation()
    try:detector=factory()
    except (ImportError,RuntimeError,OSError):return False
    tracker=Tracks(); deadline=time.monotonic()+duration; emitted=False;next_check=time.monotonic()
    while not stop.is_set() and time.monotonic()<deadline:
        now=time.monotonic()
        if now>=next_check:
            # Return to the session owner so model changes also reset track IDs
            # and spatial rule state, without restarting the recorder.
            if generation()!=initial_generation:return emitted
            next_check=now+5
        if not SLOTS.acquire(timeout=.2):continue
        try:
            frame=sampler(slug)
            stamp=int(time.time()*1000)
            if frame is not None:
                try: detections=detector.detect(frame)
                except Exception as exc: raise RuntimeError('local inference failed') from exc
                objects=tracker.observe(detections,stamp)
                emit(stamp,objects);emitted=True
            else:tracker.reset()
        finally:SLOTS.release()
        stop.wait(.6)
    return emitted
