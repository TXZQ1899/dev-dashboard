"""Persistent App Atlas labels, independent of collected ECS snapshots."""
import json


def identity(row):
    return json.dumps([row.get('tenantId') or 'legacy-aliyun', row.get('region') or '', row['id']], ensure_ascii=False, separators=(',', ':'))


def update(data, snapshot, payload):
    resource = payload.get('resource')
    key, value, action = payload.get('key'), payload.get('value'), payload.get('action', 'add')
    if not isinstance(resource, str) or not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValueError('标签键无效（1-128 个字符）')
    key = key.strip()
    matches = [row for row in snapshot.get('instances', []) if identity(row) == resource]
    if len(matches) != 1:
        raise ValueError('实例不存在或身份不唯一，请刷新页面')
    if action not in ('add', 'delete'):
        raise ValueError('操作无效')
    tags = data.setdefault(resource, {})
    if action == 'add':
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError('标签值无效（最多 256 个字符）')
        if key in tags or key in matches[0].get('tags', {}):
            raise ValueError('该标签键已存在')
        tags[key] = value.strip()
    else:
        if key not in tags:
            raise ValueError('人工标签不存在')
        del tags[key]
        if not tags:
            del data[resource]
    return data
