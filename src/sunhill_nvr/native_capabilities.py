"""Persist positive native capability evidence for the exact camera configuration."""
import hashlib
import json

def key(db,cid):
    row=db.execute('SELECT main_url,sub_url,onvif_url,onvif_username,onvif_password,onvif_auth FROM camera_details WHERE camera_id=?',(cid,)).fetchone()
    return hashlib.sha256(json.dumps(row,separators=(',',':')).encode()).hexdigest() if row else None

def record(db,cid,kinds):
    fingerprint=key(db,cid)
    if fingerprint:
        db.execute('DELETE FROM camera_native_capabilities WHERE camera_id=? AND fingerprint<>?',(cid,fingerprint))
        db.executemany('INSERT OR IGNORE INTO camera_native_capabilities VALUES(?,?,?)',((cid,fingerprint,k) for k in sorted(set(kinds))))

def supported(db,cid):
    fingerprint=key(db,cid)
    if not fingerprint:return set()
    return {r[0] for r in db.execute('SELECT kind FROM camera_native_capabilities WHERE camera_id=? AND fingerprint=?',(cid,fingerprint))}
