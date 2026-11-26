"""Check export evidence against the archived Nginx configuration."""

import fnmatch
import hashlib
import ipaddress
import json
import posixpath
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from server_inventory import parse_nginx, parse_tree


def expanded_nodes(nodes, files, prefix, stack=()):
    for args, children in nodes:
        if args[0] != 'include' or len(args) != 2:
            yield args, children
            continue
        pattern = posixpath.join(prefix, args[1])
        for name, content in files.items():
            if fnmatch.fnmatchcase(name, pattern):
                if name in stack:
                    raise ValueError('Nginx include cycle')
                yield from expanded_nodes(parse_tree(content), files, prefix, stack + (name,))


def server_directives(nodes, files, prefix):
    return (args for args, _ in expanded_nodes(nodes, files, prefix))


def is_default_server_handling(files, candidate):
    main = next(iter(files))
    prefix = posixpath.dirname(main)
    servers = []
    tree = parse_tree(files[candidate['file']])
    blocks = tree + [node for args, children in tree if args == ['http'] for node in children]
    for args, children in blocks:
        if args != ['server']:
            continue
        directives = list(server_directives(children, files, prefix))
        domains = [name for args in directives if args[0] == 'server_name' for name in args[1:]]
        listeners = [' '.join(args[1:]) for args in directives if args[0] == 'listen']
        if (domains, listeners) == (candidate['domains'], candidate['listen']):
            servers.append(directives)
    if len(servers) != 1:
        return False
    if any(
        (args[0] == 'location' and args[-1] == '/') or args[0] in ('return', 'rewrite', 'try_files', 'if')
        for args in servers[0]
    ):
        return False
    if not candidate['rootRequest']:
        return True
    # A server can deliberately serve its homepage without a location block.
    http = [args for directive, children in parse_tree(files[main]) if directive == ['http']
            for args in server_directives(children, files, prefix)]
    return not any(
        args[0] in ('root', 'alias', 'index', 'try_files') or args == ['autoindex', 'on']
        for args in servers[0] + http
    )


def archive_configurations(versions, candidates):
    by_instance = defaultdict(list)
    for item in candidates:
        key = item['assetId'], item['version'], item['instance'], item['checkedAt']
        by_instance[key].append(item)

    for (asset_id, version, instance, checked_at), items in by_instance.items():
        if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', version):
            continue
        archive_path = versions / version / 'nginx-configs' / (hashlib.sha256(asset_id.encode()).hexdigest() + '.json')
        try:
            archive = json.loads(archive_path.read_text(encoding='utf-8'))
            if (archive.get('assetId'), archive.get('collectedAt')) != (asset_id, checked_at):
                continue
            # Stored file order starts with the main config for each Nginx instance.
            files = {file['path']: file['content'] for file in archive['files'] if file.get('instance') == instance}
            dump = ''.join(
                f'# configuration file {name}:\n{content}\n' for name, content in files.items()
            )
            routes, warnings = parse_nginx(dump)
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
            continue
        yield files, routes, warnings, items


def confirmed_fallbacks(versions, candidates):
    confirmed = []
    for files, routes, warnings, items in archive_configurations(versions, candidates):
        if warnings:
            continue
        for item in items:
            roots = [route for route in routes if route['uri'] == '/' and route['context'] == 'http'
                     and (route['file'], route['domains'], route['listen']) == (item['file'], item['domains'], item['listen'])]
            if len(roots) != 1 or (roots[0]['directive'], roots[0]['target']) != ('static/other', item['target']):
                continue
            try:
                if is_default_server_handling(files, item):
                    confirmed.append(item['id'])
            except (ValueError, KeyError, IndexError, RecursionError):
                continue
    return confirmed


def listener_matches(listeners, port):
    if not listeners:
        return port == 80
    for entry in listeners:
        address = entry.split()[0]
        match = re.search(r'(?:^|:)(\d+)$', address)
        if match and int(match[1]) == port and (address.isdecimal() or ':' in address):
            return True
    return False


def rewrites_response(args):
    return args[0] in ('return', 'rewrite', 'try_files', 'if', 'proxy_pass', 'fastcgi_pass',
                       'grpc_pass', 'uwsgi_pass', 'scgi_pass', 'memcached_pass') or '_by_lua' in args[0]


def static_index(path, nodes, location):
    if not re.fullmatch(r'/[\w./-]+\.html?', path) or '..' in path.split('/'):
        return False
    for args, _ in nodes:
        if args[0] != 'location' or ' '.join(args[1:]) == location:
            continue
        if args[1].startswith('~') or path.startswith(args[-1].removeprefix('^~')):
            return False
    return True


def is_static_location(files, candidate):
    main = next(iter(files))
    if candidate['mainConfig'] and candidate['mainConfig'] != main:
        return False
    prefix = posixpath.dirname(main)
    port_servers = []
    matched_locations = []
    for directive, children in parse_tree(files[main]):
        if directive != ['http']:
            continue
        http = list(expanded_nodes(children, files, prefix))
        if any(rewrites_response(args) for args, _ in http):
            return False
        for args, body in http:
            if args != ['server']:
                continue
            nodes = list(expanded_nodes(body, files, prefix))
            listen = [' '.join(a[1:]) for a, _ in nodes if a[0] == 'listen']
            if not listener_matches(listen, candidate['port']):
                continue
            port_servers.append(nodes)
            domains = [name for a, _ in nodes if a[0] == 'server_name' for name in a[1:]]
            if (domains, listen) != (candidate['domains'], candidate['listen']):
                continue
            if any(rewrites_response(a) for a, _ in nodes):
                return False
            locations = [sub for a, sub in nodes if a[0] == 'location' and ' '.join(a[1:]) == candidate['uri']]
            if not locations and candidate['uri'] == '/' and candidate['rootRequest']:
                site_root = any(a[0] == 'root' for a, _ in nodes)
                indexes = [name for a, _ in nodes if a[0] == 'index' for name in a[1:]]
                if site_root and indexes and all(static_index('/' + name, nodes, '') for name in indexes):
                    locations.append([])
            matched_locations.extend((location, nodes) for location in locations)
    if len(matched_locations) != 1 or (candidate['requireSoleServer'] and len(port_servers) != 1):
        return False
    location, server = matched_locations[0]
    for args, children in expanded_nodes(location, files, prefix):
        if args[0] == 'if':
            for conditional, _ in expanded_nodes(children, files, prefix):
                # OpenResty may fetch the file on a cache miss while hits use alias/root.
                if conditional[0] == 'content_by_lua_file' and args[1:2] == ['(!-f']:
                    continue
                if conditional[0] != 'set':
                    return False
        elif args[0] == 'try_files' and args[1:-1] == ['$uri', '$uri/'] and static_index(args[-1], server, candidate['uri']):
            continue
        elif rewrites_response(args):
            return False
    return True


def confirmed_static_locations(versions, candidates):
    confirmed = []
    known_warnings = {'include 未展开：mime.types', '包含 Lua 动态逻辑；静态配置不能确定运行时域名、URI 或后端'}
    for files, routes, warnings, items in archive_configurations(versions, candidates):
        if any(warning not in known_warnings for warning in warnings):
            continue
        for item in items:
            keys = ('file', 'domains', 'listen', 'uri', 'target')
            match = any(route['directive'] == 'static/other' and route['context'] == 'http'
                        and all(route[key] == item[key] for key in keys) for route in routes)
            if match:
                try:
                    if is_static_location(files, item):
                        confirmed.append(item['id'])
                except (ValueError, KeyError, IndexError, RecursionError):
                    continue
    return confirmed


def response_actions(nodes, files, prefix, conditional=False):
    for args, children in nodes:
        if args[0] == 'if':
            yield from response_actions(expanded_nodes(children, files, prefix), files, prefix, conditional=True)
        elif args[0] == 'return' and len(args) > 1 and re.fullmatch(r'[1-5]\d\d', args[1]):
            yield int(args[1]), conditional
        elif args[0] == 'rewrite' and len(args) == 4 and args[-1] in ('permanent', 'redirect'):
            yield (301 if args[-1] == 'permanent' else 302), conditional


def http_servers(files):
    main = next(iter(files))
    prefix = posixpath.dirname(main)
    for directive, body in parse_tree(files[main]):
        if directive != ['http']:
            continue
        http = list(expanded_nodes(body, files, prefix))
        for args, children in http:
            if args == ['server']:
                yield http, list(expanded_nodes(children, files, prefix)), prefix


def matching_servers(servers, item):
    return [(http, server, prefix) for http, server, prefix in servers
            if ([name for args, _ in server if args[0] == 'server_name' for name in args[1:]],
                [' '.join(args[1:]) for args, _ in server if args[0] == 'listen']) == (item['domains'], item['listen'])]


def request_assignments(nodes, files, prefix, conditions=()):
    for args, children in nodes:
        if args[0] == 'set' and len(args) == 3:
            yield args[1], args[2], conditions
        elif args[0] == 'if':
            variables = tuple(re.findall(r'\$[A-Za-z_][\w]*', ' '.join(args[1:])))
            yield from request_assignments(expanded_nodes(children, files, prefix), files, prefix,
                                           conditions + (variables or ('条件表达式',)))


def same_upstream_members(http, assigned):
    targets = []
    for name in sorted(set(assigned)):
        definitions = [body for args, body in http if args == ['upstream', name]]
        if len(definitions) != 1 or any(args[0] == 'include' for args, _ in definitions[0]):
            return []
        members = set()
        for args, _ in definitions[0]:
            if args[0] != 'server' or len(args) < 2 or 'down' in args[2:]:
                continue
            # Only literal IP endpoints with an unambiguous transport are safe to use.
            if any(not item.startswith('weight=') for item in args[2:]):
                return []
            endpoint = urlsplit('//' + args[1])
            ip = str(ipaddress.ip_address(endpoint.hostname))
            port = endpoint.port or 80
            members.add((ip, port))
        targets.append(members)
    if not targets or not targets[0] or any(items != targets[0] for items in targets):
        return []
    return [{'host': ip, 'port': port, 'resolution': 'ip'} for ip, port in sorted(targets[0])]


def request_hints(versions, candidates):
    details = {}
    for files, routes, warnings, items in archive_configurations(versions, candidates):
        keys = ('file', 'uri', 'target')
        proxy_routes = {tuple(route[key] for key in keys) for route in routes
                        if route['directive'] == 'proxy_pass' and route['context'] == 'http'}
        try:
            servers = list(http_servers(files))
        except (ValueError, KeyError, RecursionError):
            continue
        for item in items:
            if tuple(item[key] for key in keys) not in proxy_routes:
                continue
            matches = matching_servers(servers, item)
            if len(matches) != 1:
                continue
            http, server, prefix = matches[0]
            locations = [body for args, body in server if args[0] == 'location' and ' '.join(args[1:]) == item['uri']]
            if len(locations) != 1:
                continue
            location = list(expanded_nodes(locations[0], files, prefix))
            variables = re.findall(r'\$[A-Za-z_][\w]*', item['target'])
            assignments = list(request_assignments(server + location, files, prefix))
            relevant = [entry for entry in assignments if entry[0] in variables]
            conditions = {value for _, _, tests in relevant for value in tests}
            conditions |= {value for var, _, tests in assignments if var in conditions for value in tests}
            hint = {}
            if conditions:
                hint['conditions'] = sorted(conditions)
            # Only a variable used as the entire authority, with a default assignment
            # and identical member sets in every branch, can be expanded here.
            direct = any(args == ['proxy_pass', item['target']] for args, _ in location)
            if (not warnings and direct and re.fullmatch(r'http://\$[A-Za-z_][\w]*', item['target'])
                    and any(not c for _, _, c in relevant)):
                try:
                    hint['backends'] = same_upstream_members(http, [value for _, value, _ in relevant])
                except ValueError:
                    pass
            # Nginx inherits proxy_set_header as a group from the nearest scope.
            headers = next(([args for args, _ in scope if args[0] == 'proxy_set_header'] for scope in (location, server, http)
                            if any(args[0] == 'proxy_set_header' for args, _ in scope)), [])
            hosts = [args[2] for args in headers if len(args) == 3 and args[1].lower() == 'host']
            if direct and len(hosts) == 1 and re.fullmatch(r'[\w.-]+(?::\d+)?', hosts[0]):
                hint['host'] = hosts[0].split(':')[0]
            if not warnings and direct and not hosts:
                try:
                    hint['host'] = str(ipaddress.ip_address(urlsplit(item['target']).hostname))
                except ValueError:
                    pass
            if hint:
                details[item['id']] = hint
    return details


def describe_stops(versions, candidates):
    details = {}
    for files, routes, warnings, items in archive_configurations(versions, candidates):
        if warnings:
            continue
        servers = list(http_servers(files))
        for item in items:
            keys = ('file', 'domains', 'listen', 'uri', 'target')
            if not any(route['directive'] == 'static/other' and route['context'] == 'http'
                       and all(route[key] == item[key] for key in keys) for route in routes):
                continue
            matches = matching_servers(servers, item)
            if len(matches) != 1:
                continue
            _, server, prefix = matches[0]
            locations = [sub for args, sub in server if args[0] == 'location' and ' '.join(args[1:]) == item['uri']]
            if len(locations) > 1:
                continue
            location = list(expanded_nodes(locations[0], files, prefix)) if locations else []
            actions = dict.fromkeys(response_actions(server + location, files, prefix))
            conditional_logging = any(args[0] == 'if' and any(a[0] == 'access_log' for a, _ in expanded_nodes(sub, files, prefix))
                                      for args, sub in location)
            logging_only = bool(conditional_logging) and not any(rewrites_response(args) for args, _ in server) and all(
                args[0] == 'if' and sub and all(a[0] == 'access_log' for a, _ in expanded_nodes(sub, files, prefix))
                for args, sub in location)
            proxy_locations = [' '.join(args[1:]) for args, body in server if args[0] == 'location' and any(
                child[0] == 'proxy_pass' and len(child) > 1 and '$' not in child[1]
                for child, _ in expanded_nodes(body, files, prefix))]
            php_fallback = [args[-1].split('?')[0] for args, _ in location
                            if args[0] == 'try_files' and args[1:-1] == ['$uri', '$uri/']
                            and re.fullmatch(r'/[\w/-]+\.php(?:\?\$query_string)?', args[-1])]
            details[item['id']] = {'location': bool(locations), 'actions': list(actions),
                                   'conditionalLogging': conditional_logging, 'loggingOnly': logging_only,
                                   'proxyLocations': proxy_locations,
                                   'phpFallback': php_fallback[0] if len(php_fallback) == 1 else ''}
    return details


if __name__ == '__main__':
    checks = {'fallbacks': confirmed_fallbacks, 'static': confirmed_static_locations,
              'stops': describe_stops, 'requests': request_hints}
    check = checks[sys.argv[2]] if len(sys.argv) > 2 else confirmed_fallbacks
    print(json.dumps(check(Path(sys.argv[1]), json.load(sys.stdin))))
