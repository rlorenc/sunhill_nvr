"""Optional offline COCO detector and conservative foreground candidates.

The ONNX file is administrator-provisioned; no model is downloaded or fetched
from a camera. Foreground candidates are not semantic object detections.
"""
from pathlib import Path
from .analytics_models import ROOT, trusted_model

MODEL=ROOT/'coco-yolov8.onnx'
LABELS=('person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign parking_meter bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee skis snowboard sports_ball kite baseball_bat baseball_glove skateboard surfboard tennis_racket bottle wine_glass cup fork knife spoon bowl banana apple sandwich orange broccoli carrot hot_dog pizza donut cake chair couch potted_plant bed dining_table toilet tv laptop mouse remote keyboard cell_phone microwave oven toaster sink refrigerator book clock vase scissors teddy_bear hair_drier toothbrush').split()

def category(label):
    if label=='person':return 'person'
    if label in LABELS[1:9]:return 'vehicle'
    if label in LABELS[14:24]:return 'animal'
    if label in ('backpack','handbag','suitcase'):return 'package'
    return 'object'

class CocoDetector:
    def __init__(self,cv,np,path=MODEL):
        self.cv=cv;self.np=np
        trusted_model(path)
        self.net=cv.dnn.readNetFromONNX(str(path))

    @staticmethod
    def decode(raw,cv,np,width,height,scale,pad_x,pad_y):
        raw=np.asarray(raw)
        if raw.ndim!=3 or raw.shape[0]!=1:raise ValueError('unexpected ONNX output shape')
        rows=raw[0].T if raw.shape[1]==84 else raw[0]
        if rows.ndim!=2 or rows.shape[1]!=84 or len(rows)>100000:raise ValueError('expected YOLOv8 COCO output')
        if not np.isfinite(rows).all():raise ValueError('nonfinite ONNX output')
        classes=rows[:,4:].argmax(axis=1);scores=rows[np.arange(len(rows)),4+classes]
        boxes=[];confidence=[];labels=[]
        for row,cls,score in zip(rows[scores>=.45],classes[scores>=.45],scores[scores>=.45]):
            x,y,w,h=map(float,row[:4]);left=max(0,(x-w/2-pad_x)/scale);top=max(0,(y-h/2-pad_y)/scale)
            right=min(width,(x+w/2-pad_x)/scale);bottom=min(height,(y+h/2-pad_y)/scale)
            if right-left<2 or bottom-top<2:continue
            boxes.append([left,top,right-left,bottom-top]);confidence.append(float(score));labels.append(int(cls))
        result=[]
        # Suppress duplicates within each class, never across classes.
        for cls in sorted(set(labels)):
            indices=[i for i,label in enumerate(labels) if label==cls]
            keep=cv.dnn.NMSBoxes([boxes[i] for i in indices],[confidence[i] for i in indices],.45,.45)
            for j in np.asarray(keep).reshape(-1):
                x,y,w,h=boxes[indices[int(j)]];label=LABELS[cls]
                result.append((category(label),[x/width,y/height,w/width,h/height],label))
        return result

    def detect(self,image):
        cv,np=self.cv,self.np;h,w=image.shape[:2];scale=min(640/w,640/h)
        rw,rh=round(w*scale),round(h*scale);px,py=(640-rw)//2,(640-rh)//2
        canvas=np.full((640,640,3),114,np.uint8)
        canvas[py:py+rh,px:px+rw]=cv.resize(image,(rw,rh))
        self.net.setInput(cv.dnn.blobFromImage(canvas,1/255.,(640,640),swapRB=True,crop=False))
        return self.decode(self.net.forward(),cv,np,w,h,scale,px,py)

class ForegroundObjects:
    """Detect new foreground against an initial reference, including stationary
    changes. Reset after large scene changes; do not call these semantic AI.
    """
    def __init__(self,cv,np):self.cv=cv;self.np=np;self.reference=None
    def observe(self,gray,known):
        cv,np=self.cv,self.np
        small=cv.resize(gray,(320,180),interpolation=cv.INTER_AREA)
        small=cv.GaussianBlur(small,(5,5),0)
        if self.reference is None:self.reference=small.copy();return []
        delta=cv.absdiff(small,self.reference);mask=cv.threshold(delta,28,255,cv.THRESH_BINARY)[1]
        if np.count_nonzero(mask)>mask.size*.25:
            self.reference=small.copy();return []
        mask=cv.morphologyEx(mask,cv.MORPH_OPEN,np.ones((3,3),np.uint8))
        mask=cv.morphologyEx(mask,cv.MORPH_CLOSE,np.ones((5,5),np.uint8))
        n,_,stats,_=cv.connectedComponentsWithStats(mask)
        result=[]
        for x,y,w,h,area in stats[1:n]:
            if area<80 or area>mask.size*.12 or min(w,h)<5:continue
            box=[float(x)/320,float(y)/180,float(w)/320,float(h)/180]
            # Reject overlapping known semantic objects. Proximity is still
            # available to the spatial rule engine for a person beside a bag.
            from .local_analytics import iou
            if any(iou(box,b)>.15 or (b[0]<=box[0]+box[2]/2<=b[0]+b[2] and b[1]<=box[1]+box[3]/2<=b[1]+b[3]) for _,b,_ in known):continue
            result.append(('object',box,'foreground_change'))
        return result
