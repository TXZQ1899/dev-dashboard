"""Import three static EIP CSV exports, preserving source rows and unnamed notes."""
import argparse
import csv
import ipaddress
import json
from pathlib import Path

FIELDS = dict(id='ID', name='实例名称', protection='安全防护', tags='标签', ip='IP地址',
              bindingType='绑定实例类型', bindingId='绑定实例ID', bindingName='绑定实例名称',
              status='IP状态', bandwidth='带宽', network='线路类型/网络类型',
              bandwidthPackage='带宽包服务', poolId='IP地址池ID', billing='付费类型',
              allocatedAt='分配时间', resourceGroup='资源组')
OWNERS = {'泛宥', '修平', 'thomascookjv'}


def read_records(paths):
    records, sources, seen = [], [], set()
    for path in paths:
        owner = path.name.split('-弹性公网IP')[0]
        if owner not in OWNERS:
            raise ValueError(f'Unknown source owner: {path.name}')
        count = 0
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            headers = next(reader)
            if not set(FIELDS.values()).issubset(headers):
                raise ValueError(f'Missing EIP columns: {path.name}')
            while True:
                line = reader.line_num + 1
                values = next(reader, None)
                if values is None:
                    break
                if not any(value.strip() for value in values):
                    continue
                if len(values) != len(headers):
                    raise ValueError(f'Column count mismatch: {path.name}:{line}')
                row = dict(zip(headers, values))
                record = {key: row[column].strip() for key, column in FIELDS.items()}
                ipaddress.ip_address(record['ip'])
                key = f'{owner}:{record["id"]}'
                if not record['id'] or key in seen:
                    raise ValueError(f'Duplicate or empty EIP ID: {path.name}:{line}')
                seen.add(key)
                notes = [{'column': i + 1, 'value': value.strip()} for i, value in enumerate(values)
                         if not headers[i].strip() and value.strip()]
                records.append(dict(record, key=key, owner=owner, source=path.name, row=line, notes=notes))
                count += 1
        sources.append(dict(file=path.name, owner=owner, count=count))
    if len(sources) != 3 or {source['owner'] for source in sources} != OWNERS:
        raise ValueError('All three distinct source exports are required')
    return dict(snapshotDate='2026-06-26', region='cn-shanghai', sources=sources, records=records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs=3, type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'lib/eip-snapshot.json')
    args = parser.parse_args()
    data = read_records(args.files)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({source['owner']: source['count'] for source in data['sources']}, ensure_ascii=False))
