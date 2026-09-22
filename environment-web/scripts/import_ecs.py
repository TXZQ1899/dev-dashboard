"""Import only display fields from the local ECS and ResourceGroup snapshots."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE.parent / 'ecs-export-cn-shanghai'


def addresses(*values):
    return list(dict.fromkeys(str(ip) for value in values for ip in value if ip))


def normalize(instance):
    extra = instance.get('extraProperties', {})
    tags = {}
    for tag in [*extra.get('tags', []), *instance.get('Tags', {}).get('Tag', [])]:
        tags[tag['TagKey']] = tag.get('TagValue', '')
    vpc = instance.get('VpcAttributes', {})
    private = addresses(vpc.get('PrivateIpAddress', {}).get('IpAddress', []),
                        instance.get('InnerIpAddress', {}).get('IpAddress', []),
                        [ip.get('PrivateIpAddress') for nic in instance.get('NetworkInterfaces', {}).get('NetworkInterface', [])
                         for ip in nic.get('PrivateIpSets', {}).get('PrivateIpSet', [])],
                        [nic.get('PrimaryIpAddress') for nic in instance.get('NetworkInterfaces', {}).get('NetworkInterface', [])])
    if not private:
        private = addresses([extra.get('innerIpAddress')])
    public = addresses(instance.get('PublicIpAddress', {}).get('IpAddress', []),
                       [instance.get('EipAddress', {}).get('IpAddress')],
                       [extra.get('publicIpAddress'), extra.get('eipAddress')])
    return dict(id=instance['InstanceId'], name=instance.get('InstanceName') or instance['InstanceId'],
                projectId=instance.get('ResourceGroupId') or '', cpu=instance['Cpu'],
                memoryGiB=instance['Memory'] / 1024, os=instance.get('OSName') or '未提供',
                privateIps=private, publicIps=public, tags=tags, status=instance.get('Status', 'Unknown'),
                region=instance['RegionId'], zone=instance.get('ZoneId', ''), instanceType=instance.get('InstanceType', ''))


def main():
    source = json.loads((SOURCE / 'ecs-list-all.json').read_text())
    resource = json.loads((SOURCE / 'ResourceGroup.json').read_text())['data']
    groups = resource['ResourceGroups']['ResourceGroup']
    assert len(groups) == resource['TotalCount'], 'ResourceGroup pagination is incomplete'
    rows = [normalize(i) for i in source['Instances']['Instance']]
    assert len(rows) == source['TotalCount'] == len({r['id'] for r in rows}), 'ECS count mismatch'
    result = dict(fetchedAt=source['FetchedAt'], region=source['RegionId'],
                  projects=[dict(id=g['Id'], name=g['DisplayName'], code=g['Name']) for g in groups], instances=rows)
    target = BASE / 'lib/ecs-snapshot.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(rows)} ECS, {len(groups)} projects; {sum(r["cpu"] for r in rows)} vCPU, {sum(r["memoryGiB"] for r in rows)} GiB')


if __name__ == '__main__':
    main()
