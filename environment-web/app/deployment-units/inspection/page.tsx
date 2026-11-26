import Link from 'next/link';
import { Shell } from '@/app/page';
import { EnvironmentInspection } from '@/components/environment-inspection';

export const metadata = { title: '环境巡检 · EnvScope' };

export default async function InspectionPage({ searchParams }: { searchParams: Promise<{ unitId?: string; environment?: string }> }) {
  const { unitId = '', environment = '' } = await searchParams;
  return <Shell active="deployment-units"><main className="check-page">
    <Link className="check-back" href="/deployment-units">← 返回部署单元</Link>
    <EnvironmentInspection unitId={unitId} environment={environment} />
  </main></Shell>;
}
