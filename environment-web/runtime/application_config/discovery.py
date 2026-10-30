"""Discover application configuration roots and Tomcat deployment paths."""
import posixpath
import re
import shlex
import xml.etree.ElementTree as ET


def config_roots(process):
    try:
        args = shlex.split(process.get('command', ''))
    except ValueError:
        return []
    roots = []
    base = next((a.split('=', 1)[1] for a in args if a.startswith('-Dcatalina.base=')), None)
    if base:
        roots.extend([posixpath.join(base, 'conf'), posixpath.join(base, 'webapps')])
    for arg in args:
        if arg.startswith(('-Dappid=', '-Dspring.application.name=', '--spring.application.name=')):
            name = arg.split('=', 1)[1]
            if re.fullmatch(r'[A-Za-z0-9_.-]+', name) and name not in ('.', '..'):
                roots.extend(['/var/www/webapps/'+name+'/WEB-INF', '/var/www/webapps/'+name+'/config'])
    if '-jar' in args:
        idx = args.index('-jar')
        if idx+1 < len(args):
            jar = args[idx+1]
            directory = posixpath.dirname(jar) if jar.startswith('/') else process.get('cwd', '')
            roots.extend([directory, posixpath.join(directory, 'config')])
    for arg in args:
        if arg.startswith(('--spring.config.location=', '--spring.config.additional-location=', '-Dspring.config.location=')):
            for path in arg.split('=', 1)[1].split(','):
                path = path.removeprefix('optional:').removeprefix('file:')
                if path.startswith('/'):
                    roots.append(posixpath.dirname(path) if posixpath.splitext(path)[1] else path)
    # A general cwd such as / or /opt is never scanned recursively.
    safe = []
    for root in roots:
        root = posixpath.normpath(root)
        if root.startswith('/') and len([x for x in root.split('/') if x]) >= 3 and root not in safe:
            safe.append(root)
    return safe[:6]


def tomcat_docbase_roots(content, process):
    """Resolve literal Context docBases declared in this process's server.xml."""
    try:
        args = shlex.split(process.get('command', ''))
        base = next((a.split('=', 1)[1] for a in args if a.startswith('-Dcatalina.base=')), '')
        if not base.startswith('/') or '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
            return []
        tree = ET.fromstring(content)
    except (ValueError, ET.ParseError):
        return []
    roots = []
    for host in tree.iter('Host'):
        appbase = host.get('appBase', 'webapps')
        if '${' in appbase or '\0' in appbase:
            continue
        appbase = posixpath.normpath(posixpath.join(base, appbase))
        for context in host.iter('Context'):
            docbase = context.get('docBase', '').strip()
            if not docbase or '${' in docbase or '\0' in docbase:
                continue
            path = posixpath.normpath(posixpath.join(appbase, docbase))
            if not path.startswith('/') or len([x for x in path.split('/') if x]) < 3 or path.lower().endswith('.war'):
                continue
            root = posixpath.join(path, 'WEB-INF')
            if root not in roots:
                roots.append(root)
    return roots[:16]


