"""Create business classifications from the current Codeup hierarchy (preview by default)."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db

RULE = 'codeup-business-hierarchy-v1'


def git_url(value):
    if not isinstance(value, str):
        return None
    url = urlsplit(value.strip())
    if url.scheme not in ('http', 'https') or url.hostname != 'codeup.aliyun.com' or url.query or url.fragment:
        return None
    path = url.path.strip('/')
    return path[:-4] if path.endswith('.git') else path


def plan(repos, snapshot):
    org = (repos.get('codeupConnection') or {}).get('organizationId')
    if repos.get('complete') is not True or snapshot.get('complete') is not True or not isinstance(org, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', org):
        raise ValueError('来源快照不完整或 Codeup 组织未知')
    domains, systems, candidates = set(), set(), {}
    for group in repos.get('groups', []):
        parts = group.get('path', '').split('/')
        if parts[0] != org or any(not p for p in parts):
            continue
        if len(parts) >= 2: domains.add(parts[1])
        if len(parts) >= 3: systems.add((parts[1], parts[2]))
    apps = {str(a.get('id')): a for a in snapshot.get('apps', []) if a.get('id') is not None}
    for repo in repos.get('repos', []):
        path = repo.get('path', '')
        parts = path.split('/')
        if repo.get('inCodeup') is not True or len(parts) < 4 or parts[0] != org or any(not p for p in parts) or git_url(repo.get('url')) != path:
            continue
        domain, system = parts[1:3]
        domains.add(domain); systems.add((domain, system))
        for ref in repo.get('apps') or []:
            app_id = str(ref.get('id', ''))
            app = apps.get(app_id)
            if app and git_url(app.get('repository')) == path:
                candidates.setdefault(app_id, set()).add((domain, system, path))
    assignments, ambiguous = {}, []
    for app_id, matches in candidates.items():
        targets = {(d, s) for d, s, _ in matches}
        if len(targets) == 1:
            domain, system = next(iter(targets))
            assignments[app_id] = {'domain': domain, 'system': system, 'paths': sorted(p for _, _, p in matches)}
        else:
            ambiguous.append(app_id)
    return {'organization': org, 'domains': sorted(domains), 'systems': sorted(systems),
            'assignments': assignments, 'ambiguous': sorted(ambiguous), 'applications': len(apps)}


def code(kind, org, *parts):
    return 'codeup-' + kind + '-' + hashlib.sha256(db.encode([org, *parts]).encode()).hexdigest()[:40]


def apply(store, result, version, snapshot_hash, can_apply):
    org = result['organization']; now = db.utcnow(); source = store.source_key + ':business-codeup'
    with store.connection() as conn, store.database_lock(conn, 'business-codeup'), store.database_lock(conn, store.source_key):
        conn.begin()
        try:
            if not can_apply(): raise db.CatalogError('来源版本已变化')
            previous = store.execute(conn, 'SELECT id,counts_json FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s', (source, snapshot_hash, RULE))
            if previous:
                conn.rollback(); return {'unchanged': True, 'counts': db.decode(previous[0]['counts_json'], {})}
            run = db.identifier()
            store.insert(conn, 'catalog_import_run', {'id':run, 'source_instance_key':source, 'source_version':version,
                'source_relative_path':'repositories.json + snapshot.json', 'snapshot_sha256':snapshot_hash, 'mapper_version':RULE,
                'scope_json':db.encode({'organization':org}), 'completeness_json':db.encode({'complete':True}),
                'imported_at':now, 'status':'WRITING'})
            counts = {'createdDomains':0, 'createdSystems':0, 'assignedUnits':0, 'alreadyAssigned':0, 'missingUnits':0}
            def change(kind, item_id, action, after):
                store.insert(conn, 'catalog_change', {'id':db.identifier(), 'change_batch_id':run, 'object_type':kind,
                    'object_id':item_id, 'action':action, 'after_json':db.encode(after), 'actor_type':'IMPORT',
                    'import_run_id':run, 'changed_at':now, 'reason':'按 Codeup 代码组层级及仓库应用关联建立分类'})
            domains = {}
            for domain in result['domains']:
                identity = code('domain', org, domain)
                rows = store.execute(conn, 'SELECT id,name FROM business_domain WHERE code=%s FOR UPDATE', (identity,))
                if rows:
                    if rows[0]['name'] != domain: raise db.CatalogError('业务领域身份冲突')
                    domains[domain] = rows[0]['id']; continue
                item = db.identifier(); domains[domain] = item
                store.insert(conn, 'business_domain', {'id':item, 'code':identity, 'name':domain,
                    'description':f'Codeup 代码组：{org}/{domain}', 'created_at':now, 'updated_at':now})
                change('BUSINESS_DOMAIN', item, 'CREATE', {'path':f'{org}/{domain}'}); counts['createdDomains'] += 1
            systems = {}
            for domain, system in result['systems']:
                identity = code('system', org, domain, system)
                rows = store.execute(conn, 'SELECT id,name,domain_id FROM business_system WHERE code=%s FOR UPDATE', (identity,))
                if rows:
                    if rows[0]['name'] != system or rows[0]['domain_id'] != domains[domain]: raise db.CatalogError('业务系统身份冲突')
                    systems[(domain, system)] = rows[0]['id']; continue
                item = db.identifier(); systems[(domain, system)] = item
                store.insert(conn, 'business_system', {'id':item, 'domain_id':domains[domain], 'code':identity, 'name':system,
                    'description':f'Codeup 代码组：{org}/{domain}/{system}', 'created_at':now, 'updated_at':now})
                change('BUSINESS_SYSTEM', item, 'CREATE', {'path':f'{org}/{domain}/{system}', 'domainId':domains[domain]}); counts['createdSystems'] += 1
            for app_id, target in sorted(result['assignments'].items()):
                rows = store.execute(conn, "SELECT u.id,u.system_id FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='DEVOPS_APPLICATION' AND s.native_key_hash=%s FOR UPDATE", (store.source_key, db.digest({'appId':app_id})))
                if not rows: counts['missingUnits'] += 1; continue
                if len(rows) != 1: raise db.CatalogError('应用与部署单元绑定不唯一')
                row = rows[0]; system_id = systems[(target['domain'], target['system'])]
                if row['system_id']:
                    counts['alreadyAssigned'] += 1; continue
                store.execute(conn, 'UPDATE deployment_unit SET system_id=%s,revision=revision+1,updated_at=%s WHERE id=%s AND system_id IS NULL', (system_id, now, row['id']))
                change('UNIT', row['id'], 'ASSIGN_SYSTEM', {'appId':app_id, 'systemId':system_id, 'repositoryPaths':target['paths']})
                counts['assignedUnits'] += 1
            if not can_apply(): raise db.CatalogError('来源版本已变化')
            store.update(conn, 'catalog_import_run', run, {'status':'READY', 'counts_json':db.encode(counts)})
            conn.commit(); return {'counts':counts}
        except Exception:
            conn.rollback(); raise


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); args = parser.parse_args()
    data = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
    version = json.loads((data/'current.json').read_text())['id']
    if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', version): raise ValueError('版本无效')
    paths = [data/'versions'/version/'repositories.json', data/'versions'/version/'snapshot.json']
    raw = [path.read_bytes() for path in paths]
    result = plan(*(json.loads(item) for item in raw))
    summary = {'version':version, 'domains':result['domains'], 'systems':len(result['systems']),
               'matchedApplications':len(result['assignments']), 'ambiguous':len(result['ambiguous']),
               'applications':result['applications'], 'example1540':result['assignments'].get('1540')}
    if args.apply:
        sha = db.digest([hashlib.sha256(item).hexdigest() for item in raw])
        valid = lambda: json.loads((data/'current.json').read_text())['id'] == version and all(path.read_bytes() == value for path, value in zip(paths, raw))
        summary['apply'] = apply(db.Catalog.from_environment(), result, version, sha, valid)
    print(json.dumps(summary, ensure_ascii=False))

if __name__ == '__main__': main()
