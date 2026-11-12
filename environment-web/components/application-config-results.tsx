'use client';

export type ConfigResult = { appId: string; name: string; status: string; error?: string; reason?: string; warnings?: string[] };
export type ConfigExclusion = { appId: string; name: string; category: string; markedAt: string };
type Props = { results: ConfigResult[]; exclusions?: ConfigExclusion[] | null; busy?: boolean; onUpdate?: (ids: string[], excluded: boolean) => void };

export function ApplicationConfigResults({ results, exclusions = null, busy = false, onUpdate }: Props) {
  const ready = results.filter((item) => item.status === 'ready');
  const partial = results.filter((item) => item.status === 'partial');
  const failed = results.filter((item) => item.status === 'failed');
  const skipped = results.filter((item) => item.status === 'skipped');
  const missing = failed.filter((item) => item.error === 'Codeup 未找到该仓库，或当前账号无法查看');
  const unsupported = failed.filter((item) => item.error === '仅支持 Codeup HTTPS 仓库');
  const unmatched = failed.filter((item) => item.error === '当前快照中仓库匹配数量为 0');
  const noAddress = failed.filter((item) => item.error === '当前 DevOps 快照未提供应用仓库地址');
  const otherFailed = failed.filter((item) => !missing.includes(item) && !unsupported.includes(item) && !unmatched.includes(item) && !noAddress.includes(item));
  const excluded = new Set(exclusions?.map((item) => item.appId) || []);
  const name = (item: { appId: string; name: string }) => `${item.name || item.appId}（${item.appId}）`;
  const button = (item: ConfigResult) => exclusions && onUpdate && <button className="config-exclusion-action" type="button" disabled={busy} onClick={() => onUpdate([item.appId], !excluded.has(item.appId))}>
    {excluded.has(item.appId) ? '撤销不用同步' : '标记不用同步'}
  </button>;
  const bulk = (items: ConfigResult[]) => {
    const ids = items.filter((item) => !excluded.has(item.appId)).map((item) => item.appId);
    return !!ids.length && !!exclusions && !!onUpdate && <button className="config-exclusion-action" type="button" disabled={busy} onClick={() => onUpdate(ids, true)}>本类全部标记不用同步（{ids.length}）</button>;
  };
  const failureGroup = (title: string, items: ConfigResult[], allowMark: boolean) => !!items.length && <div>
    <p>{title}（{items.length}） {allowMark && bulk(items)}</p>
    <ul>{items.map((item) => <li key={item.appId}>{name(item)}：{item.error || item.reason || '未记录具体原因'} {allowMark && button(item)}</li>)}</ul>
  </div>;
  return <>
    {!!ready.length && <details><summary>查看已完成的应用（{ready.length}）</summary>
      <ul>{ready.map((item) => <li key={item.appId}>{name(item)}</li>)}</ul>
    </details>}
    {!!partial.length && <details><summary>查看部分完成的应用及原因（{partial.length}）</summary>
      <ul>{partial.map((item) => <li key={item.appId}>{name(item)}：{item.warnings?.length ? item.warnings.join('；') : item.error || item.reason || '未记录具体原因'}</li>)}</ul>
    </details>}
    {!!failed.length && <details><summary>查看失败的应用及原因（{failed.length}）</summary>
      {failureGroup('Codeup 未找到仓库或账号无法查看', missing, true)}
      {failureGroup('仅支持 Codeup HTTPS 仓库', unsupported, true)}
      {failureGroup('当前快照中仓库匹配数量为 0', unmatched, true)}
      {failureGroup('未提供仓库地址', noAddress, true)}
      {failureGroup('其他失败', otherFailed, false)}
    </details>}
    {!!skipped.length && <details><summary>查看跳过的应用（{skipped.length}）</summary>
      {bulk(skipped)}
      <ul>{skipped.map((item) => <li key={item.appId}>{name(item)}：{item.reason || '已跳过'} {button(item)}</li>)}</ul>
    </details>}
    {!!exclusions?.length && <details><summary>不用同步的应用（{exclusions.length}）</summary>
      <p>批量同步和失败重试会跳过这些应用；单应用手动刷新仍可使用。</p>
      <ul>{exclusions.map((item) => <li key={item.appId}>{name(item)} {onUpdate && <button className="config-exclusion-action" type="button" disabled={busy} onClick={() => onUpdate([item.appId], false)}>撤销不用同步</button>}</li>)}</ul>
    </details>}
  </>;
}
