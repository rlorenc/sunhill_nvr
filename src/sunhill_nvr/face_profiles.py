"""Local, explicitly enrolled face templates. No image is retained by NVR.

Only administrators see profile labels, and removal invalidates historical
labels immediately. No unknown-person clustering or external lookup.
"""
from __future__ import annotations
import json
import math
import os
import re
import secrets
import stat
import time
from .analytics_models import ROOT,trusted_model

DIM=128
# Conservative starting policy, not a calibrated guarantee of identity.
MIN_SCORE=.55
MIN_MARGIN=.10
MAX_PROFILES=100
MAX_TEMPLATES=5
MAX_IMAGE=8*1024*1024
MAX_UPLOAD_TOTAL=20*1024*1024
MAX_UPLOAD_BODY=28*1024*1024


def normalized(values):
    values=[float(v) for v in values]
    if len(values)!=DIM or any(not math.isfinite(v) for v in values):raise ValueError('invalid face template')
    norm=math.sqrt(sum(v*v for v in values))
    if not math.isfinite(norm) or norm<1e-8:raise ValueError('invalid face template')
    return [v/norm for v in values]


def best_match(feature,templates):
    feature=normalized(feature);scores={}
    for ident,vector in templates:
        score=sum(x*y for x,y in zip(feature,vector))
        scores[ident]=max(scores.get(ident,-1),score)
    ranked=sorted(scores.items(),key=lambda row:row[1],reverse=True)
    if not ranked or ranked[0][1]<MIN_SCORE or (len(ranked)>1 and ranked[0][1]-ranked[1][1]<MIN_MARGIN):return None
    return ranked[0][0]


def templates(db):
    return [(ident,normalized(json.loads(raw))) for ident,raw in db.execute('SELECT profile_id,feature_json FROM face_templates ORDER BY profile_id,id')]


def enroll(db,label,features,*,model_id,authorize=None,profile_id=None):
    if not isinstance(label,str) or not 1<=len(label)<=80 or not label.strip() or any(ord(c)<32 for c in label):raise ValueError('invalid label')
    if not isinstance(model_id,str) or not re.fullmatch('[0-9a-f]{64}',model_id):raise ValueError('invalid model fingerprint')
    features=[normalized(f) for f in features]
    if not 1<=len(features)<=MAX_TEMPLATES:raise ValueError('one to five samples required')
    if profile_id is not None and not re.fullmatch('[0-9a-f]{32}',profile_id):raise ValueError('invalid profile')
    with db:
        db.execute('BEGIN IMMEDIATE')
        user_id=authorize(db) if authorize is not None else None
        if profile_id is None and db.execute('SELECT count(*) FROM face_profiles').fetchone()[0]>=MAX_PROFILES:raise ValueError('profile limit')
        existing=db.execute('SELECT DISTINCT model_sha256 FROM face_profiles').fetchall()
        if existing and existing!=[(model_id,)]:raise ValueError('remove old profiles before changing recognition model')
        ident=profile_id or secrets.token_hex(16)
        if profile_id is None:
            db.execute('INSERT INTO face_profiles VALUES(?,?,?)',(ident,label,model_id))
        else:
            if db.execute('UPDATE face_profiles SET label=? WHERE id=? AND model_sha256=?',(label,ident,model_id)).rowcount!=1:raise LookupError('unknown profile')
            db.execute('DELETE FROM face_templates WHERE profile_id=?',(ident,))
        db.executemany('INSERT INTO face_templates(profile_id,feature_json) VALUES(?,?)',[(ident,json.dumps(f,separators=(',',':'))) for f in features])
        db.execute("INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES(?,?,?,'face-profile',?)",(int(time.time()),user_id,'face.update' if profile_id else 'face.enroll',ident))
    return ident


def remove(db,ident,*,authorize=None):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('invalid profile')
    with db:
        db.execute('BEGIN IMMEDIATE')
        user_id=authorize(db) if authorize is not None else None
        removed=db.execute('DELETE FROM face_profiles WHERE id=?',(ident,)).rowcount==1
        if removed:db.execute("INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES(?,?,'face.remove','face-profile',?)",(int(time.time()),user_id,ident))
        return removed


def labels(db):
    return dict(db.execute('SELECT id,label FROM face_profiles'))


def inventory(db):
    return [{'id':ident,'label':label,'samples':count} for ident,label,count in db.execute(
        'SELECT p.id,p.label,count(t.id) FROM face_profiles p LEFT JOIN face_templates t ON t.profile_id=p.id GROUP BY p.id,p.label ORDER BY p.label,p.id')]


def public_objects(objects,labels=None):
    """Do not leak the profile ID or label to ordinary camera viewers."""
    result=[]
    for original in objects:
        obj=dict(original);ident=obj.pop('face_profile',None);obj.pop('face_match',None)
        if obj.get('kind')=='face' and labels is not None and ident in labels:obj['face_match']=labels[ident]
        result.append(obj)
    return result


def image_dimensions(data):
    """Read dimensions before decompressing browser uploads (JPEG or PNG only)."""
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=33 and data[8:16]==b'\x00\x00\x00\x0dIHDR':
        w,h=int.from_bytes(data[16:20],'big'),int.from_bytes(data[20:24],'big')
    elif data.startswith(b'\xff\xd8'):
        i=2
        while i<len(data):
            if data[i]!=255:raise ValueError('invalid JPEG')
            while i<len(data) and data[i]==255:i+=1
            if i>=len(data):raise ValueError('invalid JPEG')
            marker=data[i];i+=1
            if marker in (0,0xd8,0xd9,0xda) or i+2>len(data):raise ValueError('invalid JPEG')
            n=int.from_bytes(data[i:i+2],'big')
            if n<2 or i+n>len(data):raise ValueError('invalid JPEG')
            if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
                if n<8:raise ValueError('invalid JPEG')
                h,w=int.from_bytes(data[i+3:i+5],'big'),int.from_bytes(data[i+5:i+7],'big');break
            i+=n
        else:raise ValueError('missing JPEG dimensions')
    else:raise ValueError('JPEG or PNG required')
    if not 32<=w<=4096 or not 32<=h<=4096:raise ValueError('invalid image dimensions')
    return w,h


def web_images(payload):
    import base64,binascii
    if not isinstance(payload,list) or not 1<=len(payload)<=MAX_TEMPLATES:raise ValueError('one to five samples required')
    result=[];total=0
    for item in payload:
        if not isinstance(item,str) or len(item)>4*((MAX_IMAGE+2)//3):raise ValueError('invalid image size')
        try:data=base64.b64decode(item,validate=True)
        except (ValueError,binascii.Error):raise ValueError('invalid image encoding') from None
        total+=len(data)
        if not 0<len(data)<=MAX_IMAGE or total>MAX_UPLOAD_TOTAL:raise ValueError('invalid image size')
        image_dimensions(data);result.append(data)
    return result


def web_features(images):
    import cv2,numpy
    recognizer=Recognizer(cv2,numpy)
    return recognizer.model_id,[recognizer.enrollment_bytes(data) for data in images]


class Recognizer:
    def __init__(self,cv,np,*,root=ROOT):
        import hashlib
        self.cv=cv;self.np=np
        detector=trusted_model(root/'face-yunet.onnx');recognizer=trusted_model(root/'face-sface.onnx')
        digest=hashlib.sha256()
        for p in (detector,recognizer):
            with p.open('rb') as f:
                while block:=f.read(1024*1024):digest.update(block)
        self.model_id=digest.hexdigest()
        self.detector=cv.FaceDetectorYN.create(str(detector),'',(320,320),.9,.3,500)
        self.recognizer=cv.FaceRecognizerSF.create(str(recognizer),'')

    def faces(self,image):
        h,w=image.shape[:2]
        if min(w,h)<32: return []
        self.detector.setInputSize((w,h));_,faces=self.detector.detect(image)
        result=[]
        for face in [] if faces is None else faces:
            if len(face)!=15 or not self.np.isfinite(face).all():continue
            x,y,fw,fh=map(float,face[:4]);left=max(0,x);top=max(0,y);right=min(w,x+fw);bottom=min(h,y+fh)
            if right-left<30 or bottom-top<30:continue
            box=[left/w,top/h,(right-left)/w,(bottom-top)/h]
            feature=None
            if fw>=80 and fh>=80 and x>=0 and y>=0 and x+fw<=w and y+fh<=h:
                aligned=self.recognizer.alignCrop(image,face)
                feature=normalized(self.recognizer.feature(aligned).reshape(-1))
            result.append((box,feature))
        return result

    def enrollment_image(self,path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as f:
            s=os.fstat(f.fileno())
            if not stat.S_ISREG(s.st_mode) or not 0<s.st_size<=8*1024*1024:raise ValueError('invalid enrollment image')
            data=f.read(8*1024*1024+1)
        if len(data)>8*1024*1024:raise ValueError('image too large')
        return self.enrollment_bytes(data,validate_header=False)

    def enrollment_bytes(self,data,*,validate_header=True):
        if not 0<len(data)<=MAX_IMAGE:raise ValueError('invalid enrollment image')
        if validate_header:image_dimensions(data)
        image=self.cv.imdecode(self.np.frombuffer(data,self.np.uint8),self.cv.IMREAD_COLOR)
        if image is None or max(image.shape[:2])>4096:raise ValueError('invalid image dimensions')
        faces=self.faces(image)
        if len(faces)!=1 or faces[0][1] is None:raise ValueError('enroll one clear, sufficiently large face')
        return faces[0][1]


def active_templates(db,model_id):
    return [(ident,normalized(json.loads(raw))) for ident,raw in db.execute('SELECT t.profile_id,t.feature_json FROM face_templates t JOIN face_profiles p ON p.id=t.profile_id WHERE p.model_sha256=? ORDER BY t.profile_id,t.id',(model_id,))]
