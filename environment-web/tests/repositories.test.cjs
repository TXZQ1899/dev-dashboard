const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const Module = require('node:module');
const path = require('node:path');
const filename = path.resolve(__dirname, '../lib/repositories.ts');
const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText;
const mod = new Module(filename, module);
mod.filename = filename;
mod.paths = module.paths;
mod._compile(source, filename);
const { groups, summary, unmatched, repositorySnapshot, filterGroups } =
  mod.exports;
test('commit filters use commit date only and compose with pipeline association',()=>{
  const f=mod.exports.matchesRepository,now='2026-09-14T12:00:00+08:00';
  const r={apps:[{id:'app'}],lastCommittedAt:'2026-09-07T12:00:00+08:00'};
  assert.equal(f(r,'week','linked',now),true);
  assert.equal(f(r,'week','unlinked',now),false);
  assert.equal(f({...r,lastCommittedAt:'',updatedAt:now},'week','',now),false);
  assert.equal(f({...r,lastCommittedAt:''},'older','',now),false);
  assert.equal(f({...r,lastCommittedAt:'2025-09-14T12:00:00+08:00'},'year','',now),true);
  assert.equal(f({...r,lastCommittedAt:'2024-09-14T12:00:00+08:00'},'twoYears','',now),true);
  assert.equal(f({...r,lastCommittedAt:'2024-09-14T11:59:59+08:00'},'older','',now),true);
});
test('group totals reconcile with the current source snapshot', () => {
  assert.equal(groups.length, repositorySnapshot.groups.length);
  assert.equal(
    summary.total,
    repositorySnapshot.groups.reduce((n, g) => n + g.total, 0),
  );
  assert.equal(
    summary.accessible,
    groups.reduce(
      (n, g) => n + g.repos.filter((r) => r.access === '是').length,
      0,
    ),
  );
  assert.equal(
    groups.reduce((n, g) => n + g.gap, 0),
    summary.total - summary.linked,
  );
});
test('repositories and application memberships are deduplicated', () => {
  assert.equal(
    new Set(repositorySnapshot.repos.map((r) => r.url)).size,
    repositorySnapshot.repos.length,
  );
  assert.equal(
    new Set(repositorySnapshot.repos.map((r) => r.id)).size,
    repositorySnapshot.repos.length,
  );
  for (const r of repositorySnapshot.repos)
    assert.equal(new Set(r.apps.map((a) => a.id)).size, r.apps.length);
});
test('missing from accessible list does not claim repository deletion', () => {
  for (const r of repositorySnapshot.repos) {
    if (r.difference === 'devops_only') {
      assert.equal(r.access, '无法确认');
      assert.ok(r.apps.length > 0);
    }
    if (r.difference === 'codeup_only') {
      assert.equal(r.access, '是');
      assert.equal(r.apps.length, 0);
    }
    if (r.difference === 'both') {
      assert.equal(r.access, '是');
      assert.ok(r.apps.length > 0);
    }
  }
  for (const g of groups)
    assert.equal(g.unknown, Math.max(0, g.total - g.accessible - g.denied));
});
test('search, difference filters and empty results remain usable', () => {
  assert.equal(filterGroups('nonexistent-zzzzz', 'all', 'total').length, 0);
  const first = groups.find((g) => g.repos.length);
  if (first)
    assert.ok(
      filterGroups(first.repos[0].url, 'all', 'name').some(
        (g) => g.id === first.id,
      ),
    );
  assert.ok(filterGroups('', 'unused', 'total').every((g) => g.unused > 0));
  assert.ok(
    filterGroups('', 'devops_only', 'total').every((g) => g.devopsOnly > 0),
  );
});
test('period counts use unique collected commits and calendar boundaries',()=>{
 const r={apps:[],commitHistoryComplete:true,lastCommittedAt:'2026-09-14T00:00:00Z',commitDailyCounts:{'2026-09-14':2,'2026-06-14':3,'2026-03-14':5,'2025-09-14':7,'2024-09-13':11}};
 const now='2026-09-14T12:00:00+08:00',f=mod.exports.periodCommitCount;
 assert.equal(f(r,'quarter',now),5);assert.equal(f(r,'lastYear',now),17);
 assert.equal(f(r,'year',now),7);assert.equal(f(r,'older',now),11);
 assert.equal(f({...r,commitHistoryComplete:false},'quarter',now),null);
 assert.equal(mod.exports.matchesRepository(r,'year','',now),true);
});
test('CSV includes selected count, escapes cells, and guards formulas',()=>{
 const r={name:'=formula',groupName:'a,"b',url:'https://example.com/repo',apps:[],difference:'codeup_only',commits:10,commitHistoryComplete:true,commitDailyCounts:{'2026-09-14':3}};
 const csv=mod.exports.repositoryCsv([r],'quarter','条件','2026-09-14T00:00:00Z');
 assert.ok(csv.startsWith('\uFEFF'));assert.ok(csv.includes('"\'=formula"'));assert.ok(csv.includes('"a,""b"'));
 assert.ok(csv.includes('"最近 3 个月","3"'));assert.equal(csv.split('\r\n').length,3);
});

test('internal GitLab URLs use HTTP across snapshot and export without changing other hosts', () => {
  const {normalizeRepositoryUrl, repositoryCsv}=mod.exports;
  const http='http://gitlab.dev.thomascook.com.cn/tc-tims/finance-center.git';
  assert.equal(normalizeRepositoryUrl(http.replace('http:', 'https:')),http);
  assert.equal(normalizeRepositoryUrl(http),http);
  for(const url of ['https://codeup.aliyun.com/team/repo.git','https://gitlab.dev.thomascook.com.cn.example.com/repo.git','git@gitlab.dev.thomascook.com.cn:team/repo.git']) assert.equal(normalizeRepositoryUrl(url),url);
  const repos=repositorySnapshot.repos.filter(r=>r.url.includes('gitlab.dev.thomascook.com.cn/'));
  assert.ok(repos.length);
  assert.ok(repos.every(r=>r.url.startsWith('http://')));
  assert.ok(repositoryCsv(repos,'','').includes(http));
});

test('repositoryNameFromUrl extracts the final path segment from all Git address forms', () => {
  const f = mod.exports.repositoryNameFromUrl;
  assert.equal(f('https://codeup.aliyun.com/61a9/tc_minip/tc-taicang.git'), 'tc-taicang');
  assert.equal(f('https://codeup.aliyun.com/group/repo'), 'repo');
  assert.equal(f('git@gitlab.dev.thomascook.com.cn:team/bank-center.git'), 'bank-center');
  assert.equal(f('ssh://git@codeup.aliyun.com/x/y.git'), 'y');
  assert.equal(f('http://cat/cat'), 'cat');
  assert.equal(f('cat/cat'), 'cat');
  assert.equal(f('HTTPS://Codeup.Aliyun.com/G/TC-Taicang.GIT'), 'tc-taicang');
  assert.equal(f(''), '');
  assert.equal(f(null), '');
  assert.equal(f(undefined), '');
});

test('app repository index follows confirmed full-URL linkage without duplicates', () => {
  const index = mod.exports.appRepositoryIndex;
  assert.ok(index instanceof Map);
  let refs = 0;
  for (const r of repositorySnapshot.repos) refs += r.apps.length;
  let linked = 0;
  for (const list of index.values()) {
    linked += list.length;
    assert.equal(new Set(list.map((r) => r.id)).size, list.length);
  }
  assert.equal(linked, refs);
});

test('app repository name search covers environment-level repository overrides', () => {
  const m = mod.exports.appRepositoryNameMatches;
  // App 1865 (cat) only links to tims-uaa via its TEST override; its default
  // address http://cat/cat is an invalid reference.
  assert.equal(m('1865', [], 'tims-uaa'), true);
  assert.equal(m('1865', [], 'TIMS-UAA'), true);
  assert.equal(m('1865', [], 'foliday/tims'), true);
  // tc-taicang is codeup_only (no DevOps app): same-name guessing is not allowed.
  assert.equal(m('1865', [], 'tc-taicang'), false);
  // Fallback URLs keep search working for apps absent from the linkage index.
  assert.equal(m('unknown-app', ['https://codeup.aliyun.com/g/tc-taicang.git'], 'tc-taicang'), true);
  assert.equal(m('unknown-app', ['git@host.example:g/tc-taicang.git'], 'taicang'), true);
  assert.equal(m('unknown-app', ['https://codeup.aliyun.com/g/other.git'], 'tc-taicang'), false);
  assert.equal(m('unknown-app', ['', null, undefined], 'anything'), false);
  // Blank query disables the filter.
  assert.equal(m('1865', [], ''), true);
  assert.equal(m('1865', [], '   '), true);
});

