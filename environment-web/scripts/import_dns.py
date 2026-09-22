"""Read the two authorized DNS exports without changing their source workbooks."""
import argparse
import json
from pathlib import Path
import openpyxl

ZONES = ('folidaymall.com', 'fosunholiday.com')

def read_records(paths):
    records, sources = [], []
    for path in paths:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in workbook:
            zone = sheet.title.lower().rstrip('.')
            if zone not in ZONES:
                raise ValueError(f'Unsupported DNS zone: {zone}')
            rows = iter(sheet.values)
            headers = next(rows)
            required = ('记录类型', '主机记录', '解析线路', '记录值', '状态(暂停/启用)')
            if not all(h in headers for h in required):
                raise ValueError(f'Missing DNS columns: {path}')
            sources.append({'file': path.name, 'zone': zone})
            for number, values in enumerate(rows, 2):
                if not any(v is not None for v in values):
                    continue
                row = dict(zip(headers, values))
                def value(key):
                    return str(row.get(key) or '').strip()
                host = value('主机记录').lower().rstrip('.')
                name = zone if host == '@' else host if host == zone or host.endswith('.' + zone) else f'{host}.{zone}'
                records.append(dict(id=f'{zone}:{number}', zone=zone, name=name,
                    type=value('记录类型').upper(), value=value('记录值'), line=value('解析线路'),
                    status=value('状态(暂停/启用)'), ttl=row.get('TTL值'), weight=row.get('权重'),
                    policy=value('负载策略'), remark=value('备注'), source=path.name, row=number))
        workbook.close()
    if {s['zone'] for s in sources} != set(ZONES):
        raise ValueError('Both authorized DNS zones are required')
    return {'zones': list(ZONES), 'sources': sources, 'records': records}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs=2, type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'lib/dns-snapshot.json')
    args = parser.parse_args()
    data = read_records(args.files)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(data["records"])} DNS records')
