'use client';
import { useEffect, useRef, useState } from 'react';
import { Settings } from 'lucide-react';

export function SettingsLink() {
  const previous = useRef<string | null>(null);
  const [version, setVersion] = useState('');
  const [alerts, setAlerts] = useState<string[]>([]);
  useEffect(() => {
    let stopped = false;
    async function check() {
      try {
        const response = await fetch('/api/settings/state', {
          cache: 'no-store',
        });
        if (!response.ok) return;
        const data = (await response.json()) as { current?: { id: string }; cookies?: Record<string, { alert?: { message: string } }> };
        if (stopped) return;
        setAlerts(Object.values(data.cookies || {}).flatMap(c => c.alert ? [c.alert.message] : []));
        const current = data.current?.id;
        if (!current) return;
        if (previous.current && previous.current !== current) {
          window.location.reload();
          return;
        }
        previous.current = current;
        setVersion(current);
      } catch {
        /* Keep the current page usable during a restart. */
      }
    }
    void check();
    const timer = setInterval(check, 10000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);
  return (
    <>
    {alerts.length > 0 && <div role="alert" style={{position:'fixed',bottom:20,right:20,zIndex:1000,maxWidth:420,padding:16,border:'1px solid #dc2626',borderRadius:8,background:'#fff1f2',color:'#991b1b',boxShadow:'0 4px 16px #0002'}}>
      {alerts.map(message => <p key={message}>{message}</p>)}
      <a href="/settings#cookie-settings" style={{fontWeight:700,textDecoration:'underline'}}>前往设置更新 Cookie</a>
    </div>}
    <a
      href="/settings"
      className="settings-link"
      title={version ? `当前数据版本：${version}` : '数据同步设置'}
    >
      <Settings size={17} />
      Settings
    </a>
    </>
  );
}
