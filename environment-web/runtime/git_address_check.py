"""On-demand, read-only checks of Git references in one DevOps snapshot."""
from check_codeup_access import Checker, normalize_url
from aliyun_openapi import _codeup_client, _codeup_path_status


class GitCheckAuthenticationError(Exception):
    """The Codeup session cannot be used to complete the check."""


def check_snapshot(snapshot, saved, progress=lambda done, total: None):
    references = {}
    for app in snapshot.get('apps', []):
        urls = {app.get('repository') or ''}
        for rows in (app.get('envs') or {}).values():
            urls.update(row.get('repository') or '' for row in rows or [])
        for raw in sorted(urls):
            if not raw.strip():
                continue
            try:
                host, path, canonical = normalize_url(raw)
                key = canonical
                item = {'host': host, 'path': path, 'url': canonical, 'error': ''}
            except (ValueError, TypeError):
                key = 'invalid:' + raw
                item = {'error': 'Git 地址格式无法识别'}
            entry = references.setdefault(key, {'item': item, 'apps': []})
            entry['apps'].append({'id': str(app.get('id', '')), 'url': raw})

    def value(key):
        return (saved.get(key) or {}).get('value', '')

    mode = value('aliyun_auth_mode') or 'cookie'
    if mode == 'accesskey':
        if not value('aliyun_accesskey_id') or not value('aliyun_accesskey_secret'):
            raise ValueError('请先在设置中配置阿里云 AccessKey')
        client = _codeup_client(value('aliyun_accesskey_id'), value('aliyun_accesskey_secret'))
    else:
        cookie = value('aliyun')
        if not cookie or '\n' in cookie or '\r' in cookie:
            raise ValueError('请先在设置中配置有效的阿里云 Cookie')
        client = Checker(cookie)
    results = {}
    for index, entry in enumerate(references.values(), 1):
        item = entry['item']
        if item.get('error'):
            status, reason = 'unknown', item['error']
        elif item['host'] != 'codeup.aliyun.com':
            status, reason = 'unknown', '非 Codeup 地址，未检查'
        else:
            try:
                if mode == 'accesskey':
                    org = value('codeup_organization_id') or item['path'].split('/')[0]
                    outcome = _codeup_path_status(client, org, item['path'])
                    access, reason = outcome['access'], outcome['reason']
                    status = {'是': 'valid', '地址不存在': 'missing', '暂无权限访问': 'denied'}.get(access, 'unknown')
                else:
                    access, reason, _, code, stopped = client.check(item)
                    if stopped:
                        raise GitCheckAuthenticationError('Codeup 登录失效或遇到安全验证，请检查阿里云 Cookie 后重试')
                    status = 'valid' if access == '是' else 'denied' if code.upper() in ('FORBIDDEN', 'ACCESS_DENIED', '403') else 'missing' if code.upper() in ('NOT_FOUND', 'PROJECT_NOT_FOUND', '404') else 'unknown'
            except GitCheckAuthenticationError:
                raise
            except Exception:
                status, reason = 'unknown', '检查失败，请核对网络及凭据'
        for app in entry['apps']:
            results.setdefault(app['id'], []).append({'url': app['url'], 'status': status, 'reason': reason})
        progress(index, len(references))
    return results
