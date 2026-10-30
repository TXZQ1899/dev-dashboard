"""Extract endpoint declarations from configuration content."""
import ipaddress
import re
from urllib.parse import urlsplit


def normal_ip(value):
    try:
        ip = ipaddress.ip_address(str(value).split('%')[0])
        return str(ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip)
    except ValueError:
        return None


def config_kind(key, scheme=''):
    value = (key+' '+scheme).lower()
    for name, pattern in (('MySQL', 'mysql'), ('PostgreSQL', 'postgres'), ('Redis', 'redis'),
                          ('Elasticsearch', r'elasticsearch|\bes\b'), ('Kafka', 'kafka'),
                          ('RabbitMQ', r'rabbit|amqp'), ('RocketMQ', 'rocket'), ('ActiveMQ', 'activemq')):
        if re.search(pattern, value):
            return name
    return '未知服务'


def parse_config(content, path):
    """Extract only endpoint declarations, never passwords or arbitrary values."""
    found, groups, stack = [], {}, []
    def add(host, port, key, line, kind):
        host = host.strip('[]')
        if not re.fullmatch(r'[A-Za-z0-9_.:%-]+', host) or not str(port).isdigit() or not 0 < int(port) <= 65535:
            return
        found.append({'host': normal_ip(host) or host.lower(), 'port': int(port), 'protocol': 'tcp',
                      'kind': kind, 'file': path, 'key': key[:160], 'line': line})
    for number, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(('#', '!', '<!--')):
            continue
        indent = len(raw)-len(raw.lstrip())
        prop = re.match(r'([\w.-]+)\s*[:=]\s*(.*?)\s*$', line)
        xml = re.search(r'<(?:property|entry)\b[^>]*(?:name|key)=[\"\']([^\"\']+)[\"\'][^>]*(?:value=[\"\']([^\"\']*)[\"\']|>([^<]+))', line)
        if prop:
            key, value = prop.groups()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            key = '.'.join([x[1] for x in stack]+[key])
            if not value:
                stack.append((indent, prop[1]))
                continue
        elif xml:
            key, value = xml[1], xml[2] or xml[3] or ''
        else:
            key, value = '.'.join(x[1] for x in stack), line.lstrip('- ').strip()
        if re.search(r'password|passwd|secret|token|username', key, re.I):
            continue
        if '${' in value:
            continue
        # URLs may have credentials or query parameters: retain just hostname/port.
        for match in re.finditer(r'(?:jdbc:)?([a-zA-Z][\w+.-]*)://([^\s\"\'<>]+)', value):
            scheme, authority = match.groups()
            authority = authority.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0].rsplit('@', 1)[-1]
            default = {'redis': 6379, 'rediss': 6379, 'mysql': 3306, 'postgresql': 5432, 'amqp': 5672, 'amqps': 5671}.get(scheme)
            for item in authority.split(','):
                try:
                    parsed = urlsplit(scheme+'://'+item)
                    if parsed.hostname and (parsed.port or default):
                        add(parsed.hostname, parsed.port or default, key, number, config_kind(key, scheme))
                except ValueError:
                    continue
        if re.search(r'host|port|address|server|nodes|bootstrap|broker|url|uri|jdbc|datasource|redis|elastic|rabbit|kafka|mq', key, re.I):
            # Lists such as bootstrap.servers=broker-a:9092,broker-b:9092.
            plain_value = re.sub(r'(?:jdbc:)?[a-zA-Z][\w+.-]*://[^\s\"\'<>]+', '', value)
            for match in re.finditer(r'(?<![\w@])((?:\[[0-9a-fA-F:]+\])|[\w.-]+):(\d{1,5})(?!\d)', plain_value):
                add(match[1], match[2], key, number, config_kind(key))
            group, _, field = key.rpartition('.')
            if field in ('host', 'hostname', 'port'):
                groups.setdefault(group, {})[field] = (value.strip('\"\' '), number, key)
    for group, fields in groups.items():
        host = fields.get('host') or fields.get('hostname')
        port = fields.get('port')
        if host and port:
            add(host[0], port[0], host[2], host[1], config_kind(group))
    unique = {(x['host'], x['port'], x['file'], x['key'], x['kind']): x for x in found}
    return list(unique.values())[:200]


