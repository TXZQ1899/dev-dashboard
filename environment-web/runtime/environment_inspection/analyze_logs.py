"""Analyze downloaded archives locally; never extract archive paths to disk."""
import collections
import datetime as dt
import gzip
import io
import ipaddress
import tarfile
import re

ACCESS = re.compile(r'(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+\S+\s+\S+\s+\[(?P<date>[^]]+)\]\s+"(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?P<uri>\S+)\s+HTTP/[^\"]+"\s+(?P<status>\d{3})', re.I)
SPRING = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:[.,]\d+)?\s+(ERROR|WARN)\b')
MAX_BYTES = 256*1024*1024


def analyze(path, start, end):
    counts = collections.Counter(); clients = collections.defaultdict(set); hours = collections.Counter(); errors = collections.Counter(); files = []; warnings = []; budget = [0]
    def lines(name, stream):
        row = {'file':name, 'lines':0, 'parsed':0, 'inWindow':0, 'error':''}; files.append(row)
        try:
            for raw in stream:
                budget[0] += len(raw)
                if budget[0] > MAX_BYTES: raise ValueError('解压内容超过 256 MiB 限额')
                row['lines'] += 1
                text = raw.decode('utf-8', 'replace')
                spring = SPRING.search(text)
                if spring:
                    try:
                        timestamp = dt.datetime.strptime(spring.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=end.tzinfo)
                        if start <= timestamp <= end: errors[(timestamp.strftime('%Y-%m-%d %H:00 %z'),spring.group(2))] += 1
                    except ValueError: pass
                match = ACCESS.search(text)
                if not match: continue
                try:
                    ipaddress.ip_address(match.group('ip'))
                    stamp = dt.datetime.strptime(match.group('date'), '%d/%b/%Y:%H:%M:%S %z')
                except ValueError: continue
                row['parsed'] += 1
                if not start <= stamp <= end: continue
                row['inWindow'] += 1
                uri = match.group('uri').split('?',1)[0]
                key = (match.group('method').upper(),uri,match.group('status'))
                counts[key] += 1; clients[key].add(match.group('ip'))
                hours[stamp.astimezone(end.tzinfo).strftime('%Y-%m-%d %H:00 %z')] += 1
        except (OSError, EOFError, ValueError) as exc: row['error'] = type(exc).__name__ + ': ' + str(exc)[:100]
    def archive(name, stream, depth):
        try:
            with tarfile.open(fileobj=stream,mode='r:*') as tar:
                for member in tar:
                    if not member.isfile(): continue
                    full = name + ':' + member.name
                    if member.size > MAX_BYTES or budget[0] >= MAX_BYTES:
                        warnings.append(full+': 解压内容超过限额'); continue
                    source = tar.extractfile(member)
                    if source is None: continue
                    if member.name.endswith(('.tar.gz','.tgz','.tar')):
                        if depth >= 2: warnings.append(full+': 嵌套归档层数超限'); continue
                        data = source.read(MAX_BYTES+1)
                        if len(data)>MAX_BYTES: warnings.append(full+': 归档过大'); continue
                        archive(full, io.BytesIO(data), depth+1)
                    elif member.name.endswith('.gz'):
                        with gzip.GzipFile(fileobj=source) as uncompressed: lines(full, uncompressed)
                    elif re.search(r'\.(?:log|out|txt)(?:[.\-].*)?$',member.name,re.I): lines(full, source)
                    else: warnings.append(full+': 不支持的文件类型')
        except (OSError, EOFError, tarfile.TarError) as exc: warnings.append(name+': 归档读取失败 '+type(exc).__name__)
    with open(path, 'rb') as source: archive(str(path.name), source, 0)
    if not any(f['parsed'] for f in files): warnings.append('未识别标准 Apache/Nginx access 日志格式，无法判断 API 访问量')
    return {'files':files, 'apis':[{'method':k[0], 'uri':k[1], 'status':k[2], 'count':v, 'clients':sorted(clients[k])} for k,v in counts.most_common()], 'hours':[{'hour':k, 'count':v} for k,v in sorted(hours.items())], 'errors':[{'hour':k[0], 'level':k[1], 'count':v} for k,v in sorted(errors.items())], 'warnings':warnings}
