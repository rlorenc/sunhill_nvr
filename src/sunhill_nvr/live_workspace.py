"""Per-account live workspace, using the existing SQLite database and ACL."""
import json
from .security import may_access

LIMIT = 1000


def validate(data):
    if not isinstance(data, dict) or not {'layout_id', 'order', 'sizes'} <= set(data) <= {'layout_id', 'order', 'sizes', 'detection_boxes'}:
        raise ValueError('Nieprawidłowy układ podglądu')
    if 'detection_boxes' in data and type(data['detection_boxes']) is not bool:
        raise ValueError('Nieprawidłowa widoczność ramek detekcji')
    lid, order, sizes = data['layout_id'], data['order'], data['sizes']
    def identifier(value):
        return type(value) is int and 0 < value <= 9999999999
    if lid is not None and not identifier(lid):
        raise ValueError('Nieprawidłowy identyfikator układu')
    if not isinstance(order, list) or len(order) > LIMIT or not all(map(identifier, order)) or len(set(order)) != len(order):
        raise ValueError('Nieprawidłowa kolejność kamer')
    if not isinstance(sizes, dict) or len(sizes) > LIMIT:
        raise ValueError('Nieprawidłowe wymiary kamer')
    valid_keys = {str(cid) for cid in order}
    for key, size in sizes.items():
        if key not in valid_keys or not isinstance(size, dict) or not size or not set(size) <= {'span', 'height'}:
            raise ValueError('Nieprawidłowe wymiary kamer')
        for name, value in size.items():
            low, high = (1, 12) if name == 'span' else (140, 900)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Nieprawidłowe wymiary kamer')
    return data


def accessible(db, user, data):
    """Remove references to deleted presets and cameras after ACL changes."""
    lid = data['layout_id']
    if lid is not None and not db.execute('SELECT 1 FROM saved_layouts WHERE id=? AND user_id=?', (lid, user[0])).fetchone():
        lid = None
    order = [cid for cid in data['order'] if may_access(db, user, cid)]
    keys = {str(cid) for cid in order}
    state = {'layout_id': lid, 'order': order, 'sizes': {key: size for key, size in data['sizes'].items() if key in keys}}
    if 'detection_boxes' in data:
        state['detection_boxes'] = data['detection_boxes']
    return state


def read(db, user):
    row = db.execute('SELECT state_json FROM live_workspaces WHERE user_id=?', (user[0],)).fetchone()
    if not row:
        return None
    return accessible(db, user, validate(json.loads(row[0])))


def write(db, user, data):
    data = validate(data)
    filtered = accessible(db, user, data)
    if filtered != data:
        raise PermissionError('Brak dostępu do kamery lub układu')
    if 'detection_boxes' not in data:
        previous = read(db, user) or {}
        if 'detection_boxes' in previous:
            data = {**data, 'detection_boxes': previous['detection_boxes']}
    db.execute('INSERT INTO live_workspaces(user_id,state_json) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json',
               (user[0], json.dumps(data, separators=(',', ':'))))
