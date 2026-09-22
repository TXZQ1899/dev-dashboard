import type { Metadata } from "next";
export const metadata: Metadata = {
  title: "代码库全景分析 · EnvScope",
  description: "Codeup 代码组、仓库资产、DevOps 关联与访问权限分析。",
  openGraph: {
    title: "代码库全景分析 · EnvScope",
    description: "Codeup 代码组、仓库资产、DevOps 关联与访问权限分析。",
    images: [],
  },
  twitter: {
    card: "summary",
    title: "代码库全景分析 · EnvScope",
    description: "Codeup 代码组、仓库资产、DevOps 关联与访问权限分析。",
    images: [],
  },
};
export default function RepositoryLayout({ children }: { children: React.ReactNode }) {
  return children;
}
