"""Pure App Manage matrix logic: row order, highest version, outdated flag, shown action."""

OPEN_STATES = ('queued', 'delivered', 'running')
SIZE_KEYS = ('app_bytes', 'data_bytes', 'cache_bytes')
CELL_ACTION_KEYS = ('id', 'action', 'source', 'state', 'error_code', 'error_text', 'created_at', 'updated_at')


def total_bytes(entry):
    """app+data+cache of an inventory entry, None when unknown."""
    if entry is None or any(entry[k] is None for k in SIZE_KEYS):
        return None
    return sum(entry[k] for k in SIZE_KEYS)


def _sorted(packages, size_of):
    # Known sizes descending, unknown after every known one, package ascending breaks ties.
    return sorted(packages, key=lambda p: (size_of(p) is None, -(size_of(p) or 0), p))


def row_order(apps, primary_id, secondary_ids):
    """apps = {package: {device_id: entry}}; returns packages in display order."""
    def on_primary(p):
        return primary_id is not None and primary_id in apps[p]

    def secondary_total(p):
        totals = [total_bytes(apps[p].get(d)) for d in secondary_ids]
        known = [t for t in totals if t is not None]
        return max(known) if known else None

    off = [p for p in apps if not on_primary(p)]
    on = [p for p in apps if on_primary(p)]
    return _sorted(off, secondary_total) + _sorted(on, lambda p: total_bytes(apps[p][primary_id]))


def highest(package, inventories, tracked):
    """Highest version across devices and the latest Relume build; build wins only when strictly higher."""
    best = None
    for inv in inventories.values():
        entry = inv.get(package)
        if entry is not None and (best is None or entry['version_code'] > best['version_code']):
            best = {'version_code': entry['version_code'], 'version_name': entry['version_name'], 'from': 'device'}
    build = tracked.get(package)
    if build is not None and build['version_code'] is not None \
            and (best is None or build['version_code'] > best['version_code']):
        best = {'version_code': build['version_code'], 'version_name': build['version_name'], 'from': 'build'}
    return best


def shown_actions(actions):
    """{(device_id, package): action}: the open action, else the newest action when it is failed."""
    newest, open_ = {}, {}
    for a in actions:
        key = (a['device_id'], a['package'])
        if key not in newest or a['id'] > newest[key]['id']:
            newest[key] = a
        if a['state'] in OPEN_STATES and (key not in open_ or a['id'] > open_[key]['id']):
            open_[key] = a
    shown = {k: a for k, a in newest.items() if a['state'] == 'failed'}
    shown.update(open_)
    return shown


def _first(package, devices, inventories, field):
    for d in devices:
        entry = inventories.get(d['id'], {}).get(package)
        if entry is not None and entry[field] is not None:
            return entry[field]
    return None


def build(devices, inventories, actions, tracked, primary_id):
    shown = shown_actions(actions)
    packages = {p for inv in inventories.values() for p in inv} | {p for _, p in shown}
    apps = {p: {d: inv[p] for d, inv in inventories.items() if p in inv} for p in packages}
    ids = [d['id'] for d in devices]
    order = row_order(apps, primary_id, [i for i in ids if i != primary_id])
    label_order = sorted(devices, key=lambda d: d['id'] != primary_id)  # stable: primary, then `devices` order
    rows = []
    for p in order:
        top = highest(p, inventories, tracked)
        ref = tracked.get(p)
        cells = {}
        for d in ids:
            entry = inventories.get(d, {}).get(p)
            action = shown.get((d, p))
            installed = None
            if entry is not None:
                installed = {k: entry[k] for k in ('version_code', 'version_name') + SIZE_KEYS + ('size_reason',)}
                installed['outdated'] = entry['version_code'] < top['version_code']
            cells[str(d)] = {
                'installed': installed,
                'action': None if action is None else {k: action[k] for k in CELL_ACTION_KEYS},
            }
        rows.append({
            'package': p,
            'label': _first(p, label_order, inventories, 'label') or p,
            'icon': _first(p, label_order, inventories, 'icon_sha256'),
            'group': 'on_primary' if primary_id in apps[p] else 'off_primary',
            'source': 'build' if ref is not None else 'play_direct',
            'ref': None if ref is None else {'kind': ref['kind'], 'id': ref['id']},
            'highest': top,
            'cells': cells,
        })
    return {'primary_id': primary_id, 'devices': devices, 'apps': rows}

