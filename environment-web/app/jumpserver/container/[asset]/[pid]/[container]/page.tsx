import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Shell } from '@/app/page';
import { ContainerInfo } from '@/components/container-info';
import { jumpserver } from '@/lib/jumpserver';
import type { Asset } from '@/lib/server-processes';

export const metadata = { title: '容器信息 · EnvScope' };
export default async function ContainerInfoPage({ params }: { params: Promise<{ asset: string; pid: string; container: string }> }) {
  const { asset: assetId, pid: rawPid, container } = await params;
  const asset = (jumpserver.assets as Asset[]).find(row => row.id === assetId);
  const pid = Number(rawPid);
  if (!asset || !Number.isSafeInteger(pid) || pid <= 0 || !asset.inspection?.processes.some(p => p.pid === pid) ||
      !asset.inspection.docker?.containers.some(c => c.id === container)) notFound();
  return <Shell active="jumpserver"><main className="check-page">
    <Link className="check-back" href="/jumpserver">← 返回 JumpServer</Link>
    <ContainerInfo asset={asset.id} ip={asset.ip} pid={pid} containerId={container}/>
  </main></Shell>;
}
