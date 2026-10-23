import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'EnvScope · 应用环境管理',
  description: '应用环境总览、服务器清单与生产单点分析',
  openGraph: {
    title: 'EnvScope · 应用环境管理',
    description: '应用环境总览、服务器清单与生产单点分析',
    images: ['/og.png'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'EnvScope · 应用环境管理',
    description: '应用环境总览、服务器清单与生产单点分析',
    images: ['/og.png'],
  },
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
