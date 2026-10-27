'use client';
import { useState } from 'react';
import { ApplicationCheck } from '@/components/application-check';
import { ApplicationFiles } from '@/components/application-files';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

export function ApplicationInspection({ asset, ip, pid, name }: {
  asset: string; ip: string; pid: number; name: string;
}) {
  const [visited, setVisited] = useState<string[]>(['tcp']);
  return <section className="application-check-panel" aria-label="单应用上下游检测">
    <div className="check-heading"><div><h2>单应用上下游检测</h2><p>{ip} · PID {pid} · {name}</p></div></div>
    <Tabs defaultValue="tcp" className="check-capability-tabs" onValueChange={(value) => setVisited((previous) => previous.includes(String(value)) ? previous : [...previous, String(value)])}>
      <TabsList aria-label="应用检测能力" variant="line">
        <TabsTrigger value="tcp">TCP 连接检测</TabsTrigger>
        <TabsTrigger value="config">配置文件</TabsTrigger>
        <TabsTrigger value="log">日志文件</TabsTrigger>
      </TabsList>
      <TabsContent value="tcp" keepMounted><ApplicationCheck asset={asset} pid={pid} /></TabsContent>
      <TabsContent value="config" keepMounted>{visited.includes('config') && <ApplicationFiles asset={asset} pid={pid} kind="config" />}</TabsContent>
      <TabsContent value="log" keepMounted>{visited.includes('log') && <ApplicationFiles asset={asset} pid={pid} kind="log" />}</TabsContent>
    </Tabs>
  </section>;
}
