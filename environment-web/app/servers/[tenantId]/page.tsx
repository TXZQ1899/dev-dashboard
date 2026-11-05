'use client';
import { useParams } from 'next/navigation';
import EcsView from '../../aliyun/ecs-view';
export default function Page() {
  const { tenantId } = useParams<{ tenantId: string }>();
  return <EcsView detail tenantId={tenantId} />;
}
