import Link from 'next/link';
import { Shell } from '@/app/page';
import { SavedApplicationConfig } from './saved-application-config';

export const metadata = { title: '应用配置 · EnvScope' };

export default async function ApplicationConfigPage({ params }: { params: Promise<{ appId: string }> }) {
  const { appId } = await params;
  return <Shell active="deployment-units"><main className="check-page">
    <Link className="check-back" href="/deployment-units">← 返回部署单元</Link>
    <SavedApplicationConfig appId={appId} />
  </main></Shell>;
}
