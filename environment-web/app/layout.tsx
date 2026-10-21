import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'AppAtlas · 应用架构图谱',
  description: '业务领域、系统、部署单元、部署实例与请求链路的应用架构图谱',
  openGraph: {
    title: 'AppAtlas · 应用架构图谱',
    description: '业务领域、系统、部署单元、部署实例与请求链路的应用架构图谱',
    images: ['/og.png'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AppAtlas · 应用架构图谱',
    description: '业务领域、系统、部署单元、部署实例与请求链路的应用架构图谱',
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
