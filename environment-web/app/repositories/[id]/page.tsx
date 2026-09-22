import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Shell } from "../../page";
import { repositorySnapshot, groups, differenceLabel, formatRepoTime } from "@/lib/repositories";
import { AccessBadge } from "../page";
import "../repositories.css";

type Props = { params: Promise<{ id: string }> };
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const repo = repositorySnapshot.repos.find((r) => r.id === id);
  const title = repo ? `${repo.name} · 代码库详情` : "未找到代码库";
  const description = repo
    ? `${repo.name} 的关联应用、代码组及已核验访问状态。`
    : "该代码库不在当前数据快照中。";
  return {
    title,
    description,
    openGraph: { title, description, images: [] },
    twitter: { card: "summary", title, description, images: [] },
  };
}
export default async function RepositoryDetail({ params }: Props) {
  const { id } = await params;
  const repo = repositorySnapshot.repos.find((r) => r.id === id);
  if (!repo) notFound();
  const group = groups.find((g) => g.id === repo.groupId);
  let external = "";
  try {
    const url = new URL(repo.url);
    if (url.protocol === "https:" || url.protocol === "http:")
      external = repo.url.replace(/\.git\/?$/, "");
  } catch {
    /* Unsupported addresses remain text. */
  }
  return (
    <Shell active="repos">
      <main className="repo-dashboard">
        <a className="repo-detail-back" href="/repositories">
          ← 返回代码库全景
        </a>
        <div className="page-heading">
          <div>
            <div className="eyebrow">REPOSITORY DETAIL</div>
            <h1>{repo.name}</h1>
            <p>仓库资产快照 · 关联关系与网页读取权限</p>
          </div>
          <AccessBadge value={repo.access} />
        </div>
        <div className="repo-detail-grid">
          <section className="panel">
            <h2>代码库信息</h2>
            <dl>
              <dt>所属代码组</dt>
              <dd>{group?.name || "未精确匹配清单代码组"}</dd>
              <dt>完整地址</dt>
              <dd>{repo.url} {repo.url.includes('://code.aliyun.com/') ? <span className="repo-source invalid">地址失效</span> : null}</dd>
              <dt>清单对比</dt>
              <dd>{differenceLabel(repo)}</dd>
              <dt>分支数</dt>
              <dd>{repo.branches ?? "未采集"}</dd>
              <dt>合并请求数</dt>
              <dd>{repo.mergeRequests ?? "未采集"}</dd>
              <dt>提交数</dt>
              <dd>{repo.commits ?? "未采集"}</dd>
              <dt>最近代码提交时间</dt>
              <dd>{formatRepoTime(repo.lastCommittedAt)}</dd>
              <dt>组匹配状态</dt>
              <dd>{repo.match}</dd>
              <dt>我的访问权限</dt>
              <dd>
                <AccessBadge value={repo.access} />
              </dd>
              <dt>检查说明</dt>
              <dd>{repo.reason}</dd>
              <dt>权限快照日期</dt>
              <dd>{repositorySnapshot.accessDate}</dd>
            </dl>
            {external && (
              <a className="repo-address" href={external} target="_blank" rel="noopener noreferrer">
                在代码托管平台打开 ↗
              </a>
            )}
            <a className="repo-address" href={`/applications?q=${encodeURIComponent(repo.url)}`}>
              在应用列表中查找此代码库 ↗
            </a>
            {repo.possibleCodeupMatches?.map((candidate) => (
              <a className="repo-address repo-candidate" key={candidate.id} href={candidate.url.replace(/\.git\/?$/, '')} target="_blank" rel="noopener noreferrer">
                可能重命名的 Codeup 仓库：{candidate.groupName} / {candidate.name} ↗
              </a>
            ))}
            <p className="repo-notice" style={{ marginTop: 24 }}>
              访问状态来自导出账号的历史检查，不代表当前访客权限，也不保证 Git clone 或推送权限。
            </p>
          </section>
          <section className="panel">
            <h2>关联应用 · {repo.apps.length}</h2>
            {repo.apps.map((app) => (
              <div key={app.id} style={{ borderBottom: "1px solid #e4ebe6", padding: "16px 0" }}>
                <a
                  className="repo-address"
                  href={`/applications?appId=${encodeURIComponent(app.id)}#app-${encodeURIComponent(app.id)}`}
                >
                  {app.name} ↗
                </a>
                <p style={{ fontSize: 11, color: "#8a988f", marginTop: 8 }}>
                  应用 ID {app.id} · 分支 {app.branch || "未提供"}
                </p>
              </div>
            ))}
          </section>
        </div>
      </main>
    </Shell>
  );
}
