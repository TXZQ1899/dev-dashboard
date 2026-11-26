import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Shell } from '@/app/page';
import { ApplicationInspection } from '@/components/application-inspection';
import { ApplicationJvm } from '@/components/application-jvm';
import { jumpserver } from '@/lib/jumpserver';
import { processBusinessName } from '@/lib/process-comparison';
import type { Asset } from '@/lib/server-processes';

export const metadata = { title: '应用进程分析 · EnvScope' };

export default async function ApplicationCheckPage({ params }: { params: Promise<{ asset: string; pid: string }> }) {
  const { asset: assetId, pid: value } = await params;
  const asset = (jumpserver.assets as Asset[]).find((row) => row.id === assetId);
  const pid = Number(value);
  const process = asset?.inspection?.processes?.find((row) => row.pid === pid);
  if (!asset || !Number.isSafeInteger(pid) || pid <= 0 || pid > 2 ** 31 - 1) notFound();
  return <Shell active="jumpserver"><main className="check-page">
    <Link className="check-back" href="/jumpserver">← 返回 JumpServer</Link>
    {process ? <ApplicationInspection asset={asset.id} ip={asset.ip} pid={pid} name={processBusinessName(process)} />
      : <ApplicationJvm asset={asset.id} pid={pid} readOnly />}
  </main></Shell>;
}
