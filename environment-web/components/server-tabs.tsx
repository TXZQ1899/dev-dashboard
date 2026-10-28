import './server-tabs.css';
const tabs = [
  { key: 'server-overview', label: '服务器资源总览', href: '/servers' },
  { key: 'server-xiuping', label: '阿里云-上海修平', href: '/servers/xiuping' },
  { key: 'server-thomascookjv', label: '阿里云- Thomascookjv', href: '/servers/thomascookjv' },
  { key: 'datacenter', label: '本地机房', href: '/datacenter' },
];
export function ServerTabs({ active }: { active: string }) {
  return <nav className="server-tabs" aria-label="服务器资源">
    {tabs.map(tab => <a key={tab.key} href={tab.href} aria-current={active === tab.key ? 'page' : undefined}>{tab.label}</a>)}
  </nav>;
}
