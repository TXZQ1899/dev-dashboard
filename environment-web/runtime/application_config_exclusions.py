"""User-managed application IDs excluded from future batch configuration syncs."""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def category(result):
    if result.get('status') == 'skipped': return 'skipped'
    if result.get('status') != 'failed': return None
    message = result.get('error', '')
    if message == 'Codeup 未找到该仓库，或当前账号无法查看': return 'codeup_not_found'
    if message == '仅支持 Codeup HTTPS 仓库': return 'unsupported_repository'
    if message == '当前快照中仓库匹配数量为 0': return 'repository_unmatched'
    if message == '当前 DevOps 快照未提供应用仓库地址': return 'repository_missing'
    return None


def read_exclusions(data):
    path = data/'application-config-exclusions.json'
    try: value = json.loads(path.read_text())
    except FileNotFoundError: return []
    except (OSError, ValueError): raise ValueError('不用同步清单读取失败，请检查数据卷') from None
    entries = value.get('applications') if isinstance(value, dict) and value.get('schemaVersion') == 1 else None
    if not isinstance(entries, list): raise ValueError('不用同步清单格式无效')
    ids = set()
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get('appId'), str) or not re.fullmatch(r'\d+', item['appId']) or item['appId'] in ids:
            raise ValueError('不用同步清单格式无效')
        ids.add(item['appId'])
    return entries


def update_exclusions(data, results, app_ids, excluded, write):
    if not isinstance(excluded, bool) or not isinstance(app_ids, list) or not app_ids or len(app_ids) > 10000 or any(not isinstance(x,str) or not re.fullmatch(r'\d+',x) for x in app_ids) or len(set(app_ids)) != len(app_ids):
        raise ValueError('应用选择无效')
    entries = {item['appId']:item for item in read_exclusions(data)}
    if excluded:
        by_id = {item.get('appId'): item for item in results if isinstance(item, dict)}
        selected = [by_id.get(app_id) for app_id in app_ids]
        if any(not item or not category(item) for item in selected):
            raise ValueError('只能标记上次结果中指定类型的应用，请刷新后重试')
        for item in selected:
            app_id = item['appId']
            if app_id not in entries:
                entries[app_id] = {'appId':app_id, 'name':str(item.get('name') or '')[:200], 'category':category(item),
                                   'markedAt':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
                                   'snapshotVersion':str(item.get('snapshotVersion') or '')[:80]}
    else:
        for app_id in app_ids: entries.pop(app_id, None)
    ordered = sorted(entries.values(), key=lambda item: int(item['appId']))
    write(data/'application-config-exclusions.json', {'schemaVersion':1, 'applications':ordered})
    return ordered
