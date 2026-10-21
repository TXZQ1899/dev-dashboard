import Link from 'next/link';
import { Button, buttonVariants } from '@/components/ui/button';

export function ApplicationCheckLink({ asset, pid, disabled = false }: { asset: string; pid: number; disabled?: boolean }) {
  if (disabled) return <Button size="sm" variant="outline" disabled>检测上下游</Button>;
  return <Link className={buttonVariants({ size: 'sm', variant: 'outline' })}
    href={`/jumpserver/check/${encodeURIComponent(asset)}/${pid}`} target="_blank" rel="noopener noreferrer">检测上下游</Link>;
}
