"""Cloud account configuration. Secrets stay in the existing private volume file."""
from copy import deepcopy
from datetime import datetime, timezone
import re
import uuid

LEGACY_TENANT = 'legacy-aliyun'
CLOUD_SOURCES = ('ecs', 'clb', 'nat', 'dms', 'cloud-dns')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def value(saved, key):
    return (saved.get(key) or {}).get('value', '')


def normalize(saved):
    """Idempotent migration; old clients still edit the original account aliases."""
    config = saved.setdefault('aliyunTenants', {'schemaVersion': 2, 'tenants': [{
        'id': LEGACY_TENANT, 'name': '现有租户', 'accountId': '', 'enabled': True,
        'regions': ['cn-shanghai'], 'dmsTenantId': '', 'credentialVersion': 'legacy',
    }], 'codeupTenantId': LEGACY_TENANT})
    original = next(t for t in config['tenants'] if t['id'] == LEGACY_TENANT)
    auth = {'mode': value(saved, 'aliyun_auth_mode') or 'cookie',
            'accessKeyId': value(saved, 'aliyun_accesskey_id'),
            'accessKeySecret': value(saved, 'aliyun_accesskey_secret'),
            'cookie': value(saved, 'aliyun')}
    previous = original.get('auth')
    if previous is not None and previous != auth:
        original['credentialVersion'] = uuid.uuid4().hex
        original.pop('verifiedAt', None)
    original['auth'] = auth
    original['codeupOrganizationId'] = value(saved, 'codeup_organization_id')
    return config


def configured(tenant):
    auth = tenant['auth']
    return bool(auth.get('accessKeyId') and auth.get('accessKeySecret')) if auth['mode'] == 'accesskey' else bool(auth.get('cookie'))


def public_tenant(tenant):
    result = {k: deepcopy(tenant.get(k)) for k in (
        'id', 'name', 'accountId', 'enabled', 'regions', 'dmsTenantId',
        'credentialVersion', 'updatedAt', 'verifiedAt')}
    key = tenant['auth'].get('accessKeyId', '')
    result.update(mode=tenant['auth']['mode'], configured=configured(tenant),
                  accessKeyId=(key[:6] + '****' + key[-4:]) if len(key) > 12 else ('****' if key else ''),
                  codeup=tenant['id'] == LEGACY_TENANT)
    if tenant['id'] == LEGACY_TENANT:
        result['codeupOrganizationId'] = tenant.get('codeupOrganizationId', '')
    return result


def select(saved, source=None, tenant_id=None):
    if tenant_id is not None and not isinstance(tenant_id, str):
        raise ValueError('租户 ID 格式无效')
    tenants = normalize(saved)['tenants']
    if source == 'codeup':
        tenant_id = LEGACY_TENANT
    if tenant_id and not any(t['id'] == tenant_id for t in tenants):
        raise ValueError('租户不存在')
    selected = [deepcopy(t) for t in tenants if t['enabled'] and (not tenant_id or t['id'] == tenant_id)]
    if not selected:
        raise ValueError('请至少启用一个阿里云租户')
    for tenant in selected:
        if not configured(tenant):
            raise ValueError(f"租户「{tenant['name']}」缺少有效的 Cookie / AccessKey")
    return selected


def identity(auth):
    from aliyun_openapi import RpcClient
    try:
        result = RpcClient(auth['accessKeyId'], auth['accessKeySecret'],
                           'sts.aliyuncs.com', '2015-04-01').call('GetCallerIdentity')
    except Exception:
        raise ValueError('账号验证失败，请检查 AccessKey、权限和网络') from None
    account = str(result.get('AccountId') or '')
    if not re.fullmatch(r'\d{1,32}', account):
        raise ValueError('账号验证未返回有效的阿里云账号 UID')
    return account


def verify(tenant, verifier=None):
    if tenant['auth']['mode'] != 'accesskey':
        return tenant
    account = (verifier or identity)(tenant['auth'])
    if tenant.get('accountId') and tenant['accountId'] != account:
        raise ValueError('AccessKey 所属账号与租户 UID 不一致，请创建新租户')
    tenant.update(accountId=account, verifiedAt=stamp())
    return tenant


def update(saved, patches, verifier=None):
    """Apply individual upserts; a missing list member never deletes another tenant."""
    if not isinstance(patches, list) or len(patches) > 50:
        raise ValueError('无效的租户设置')
    config = normalize(saved)
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError('无效的租户设置')
        tenant_id = patch.get('id')
        old = next((t for t in config['tenants'] if t['id'] == tenant_id), None)
        if tenant_id and old is None:
            raise ValueError('租户不存在，请刷新后重试')
        if old and patch.get('credentialVersion') != old.get('credentialVersion'):
            raise ValueError('租户设置已更新，请刷新后重试')
        tenant = deepcopy(old) if old else {
            'id': str(uuid.uuid4()), 'name': '', 'accountId': '', 'enabled': True,
            'regions': ['cn-shanghai'], 'dmsTenantId': '',
            'auth': {'mode': 'accesskey', 'accessKeyId': '', 'accessKeySecret': '', 'cookie': ''}}
        for field in ('name', 'accountId', 'dmsTenantId'):
            if field in patch:
                item = patch[field]
                if not isinstance(item, str) or len(item.strip()) > 100 or any(ord(c) < 32 for c in item):
                    raise ValueError('租户名称或 ID 格式无效')
                tenant[field] = item.strip()
        if not tenant['name']:
            raise ValueError('请填写租户名称')
        for field in ('accountId', 'dmsTenantId'):
            if tenant[field] and not re.fullmatch(r'\d{1,32}', tenant[field]):
                raise ValueError('阿里云账号 UID 和 DMS Tid 必须为数字')
        if old and old.get('accountId') and tenant['accountId'] != old['accountId']:
            raise ValueError('已绑定的阿里云账号 UID 不可修改')
        if 'enabled' in patch:
            if not isinstance(patch['enabled'], bool):
                raise ValueError('无效的租户启停状态')
            tenant['enabled'] = patch['enabled']
        if 'regions' in patch:
            regions = patch['regions']
            if (not isinstance(regions, list) or not 1 <= len(regions) <= 20 or
                    any(not isinstance(r, str) or not re.fullmatch(r'[a-z]+-[a-z0-9]+(?:-[a-z0-9]+)*', r) for r in regions)):
                raise ValueError('请填写有效地域，如 cn-shanghai')
            tenant['regions'] = list(dict.fromkeys(regions))
        if 'mode' in patch:
            mode = patch['mode']
            if mode not in ('cookie', 'accesskey') or (tenant['id'] != LEGACY_TENANT and mode != 'accesskey'):
                raise ValueError('只有 Codeup 绑定租户支持 Cookie 兼容方式')
            tenant['auth']['mode'] = mode
        if 'cookie' in patch:
            cookie = patch['cookie']
            if tenant['id'] != LEGACY_TENANT or not isinstance(cookie, str):
                raise ValueError('只有 Codeup 绑定租户支持阿里云 Cookie')
            cookie = cookie.strip()
            if cookie.lower().startswith('cookie:'): cookie = cookie.split(':', 1)[1].strip()
            if cookie:
                if len(cookie) > 20000 or any(ord(c) < 32 or ord(c) > 255 for c in cookie):
                    raise ValueError('Cookie 必须是单行请求头内容')
                tenant['auth']['cookie'] = cookie
        if 'codeupOrganizationId' in patch:
            organization = patch['codeupOrganizationId']
            if tenant['id'] != LEGACY_TENANT or not isinstance(organization, str):
                raise ValueError('只有 Codeup 绑定租户可配置企业 ID')
            organization = organization.strip()
            if len(organization) > 128 or any(ord(c) < 32 or ord(c) > 126 for c in organization):
                raise ValueError('Codeup 企业 ID 格式无效')
            tenant['codeupOrganizationId'] = organization
        key, secret = patch.get('accessKeyId', ''), patch.get('accessKeySecret', '')
        if not isinstance(key, str) or not isinstance(secret, str):
            raise ValueError('AccessKey 格式无效')
        key, secret = key.strip(), secret.strip()
        if len(key) > 128 or len(secret) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in key + secret):
            raise ValueError('AccessKey 格式无效')
        if key and key != tenant['auth'].get('accessKeyId') and not secret:
            raise ValueError('更换 AccessKey ID 时请同时填写 Secret')
        if key: tenant['auth']['accessKeyId'] = key
        if secret: tenant['auth']['accessKeySecret'] = secret
        if (key or secret) and 'mode' not in patch:
            tenant['auth']['mode'] = 'accesskey'
        if tenant['enabled'] and not configured(tenant):
            raise ValueError('启用租户前请配置所选采集方式的凭据')
        if tenant['auth']['mode'] == 'cookie' and tenant['regions'] != ['cn-shanghai']:
            raise ValueError('Cookie 采集仅支持上海地域；其他地域请配置 AccessKey')
        changed = not old or tenant['auth'] != old['auth']
        if (changed or patch.get('verify') or tenant['accountId'] != (old or {}).get('accountId', '')) and configured(tenant):
            verify(tenant, verifier)
        if tenant['id'] != LEGACY_TENANT and tenant['enabled'] and not tenant.get('verifiedAt'):
            verify(tenant, verifier)
        tenant.update(credentialVersion=uuid.uuid4().hex, updatedAt=stamp())
        if old:
            config['tenants'][config['tenants'].index(old)] = tenant
        else:
            config['tenants'].append(tenant)
        if tenant['id'] == LEGACY_TENANT:
            for alias, field in (('aliyun_auth_mode', 'mode'), ('aliyun_accesskey_id', 'accessKeyId'),
                                 ('aliyun_accesskey_secret', 'accessKeySecret')):
                saved[alias] = {'value': tenant['auth'][field], 'updatedAt': tenant['updatedAt']}
            if tenant['auth']['cookie'] != value(saved, 'aliyun'):
                saved['aliyun'] = {'value': tenant['auth']['cookie'], 'updatedAt': tenant['updatedAt']}
            if 'codeupOrganizationId' in patch:
                if tenant['codeupOrganizationId']:
                    saved['codeup_organization_id'] = {'value': tenant['codeupOrganizationId'], 'updatedAt': tenant['updatedAt']}
                else:
                    saved.pop('codeup_organization_id', None)
    accounts = [t['accountId'] for t in config['tenants'] if t.get('accountId')]
    if len(accounts) != len(set(accounts)):
        raise ValueError('同一阿里云账号不能重复配置为多个租户')
    return config


def resource_key(tenant_id, source, region, native_id):
    import json
    return json.dumps([tenant_id, source, region or '', str(native_id)], ensure_ascii=False, separators=(',', ':'))
