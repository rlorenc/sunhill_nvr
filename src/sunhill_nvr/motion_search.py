"""Search already received native bounding boxes; never decode recordings."""
import json
from .camera_events import normalize_box
MAX_SECONDS=120

def scan(db,cid,record_start,start,duration,box):
    x,y,w,h=normalize_box(box);lo=round((record_start+start)*1000);hi=round((record_start+start+duration)*1000)
    hits=[];samples=0
    for stamp,raw in db.execute("SELECT stamp_ms,objects_json FROM camera_metadata_history WHERE camera_id=? AND stamp_ms>=? AND stamp_ms<? AND session LIKE 'native:%' ORDER BY stamp_ms",(cid,lo,hi)):
        samples+=1
        for obj in json.loads(raw):
            try:a,b,c,d=normalize_box(obj['box'])
            except (KeyError,TypeError,ValueError):continue
            if x<a+c and a<x+w and y<b+d and b<y+h:
                moment=max(start,stamp/1000-record_start)
                if hits and moment-hits[-1]['end']<=1.5:hits[-1]['end']=moment
                else:hits.append({'start':moment,'end':moment})
                break
    return {'hits':hits,'sample_count':samples,'source':'camera-metadata'}
