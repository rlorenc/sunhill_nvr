"""Persistent RTSP segment length without a database schema change."""
SEGMENT_SECONDS = (30, 60, 120, 300, 600, 900, 1800, 3600)
DEFAULT = 300

def segment_seconds(db):
    row=db.execute("SELECT value FROM system_settings WHERE key='recording_segment_seconds'").fetchone()
    try:value=int(row[0]) if row else DEFAULT
    except (ValueError,TypeError):return DEFAULT
    return value if value in SEGMENT_SECONDS else DEFAULT
