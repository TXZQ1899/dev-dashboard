/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { Tabs: Primitive } = require('@base-ui/react/tabs');

const progressModule = { exports: {}, require };
progressModule.require = id => id === '@/lib/datetime' ? datetimeModule.exports : require(id);

const checkModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/application-check.ts', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, checkModule);

// 组件的时间展示已统一委托给 lib/datetime.ts。
const datetimeModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/datetime.ts', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, datetimeModule);
vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-check-progress.tsx', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText, progressModule);

const endpoint = (ip, port) => ({ ip, port, protocol: 'tcp' });
const published = endpoint('10.0.0.2', 7000);
const target = endpoint('172.18.0.6', 6379);
const client = endpoint('10.0.0.1', 33580);
const application = { appId: null, appName: null, confidence: 'UNKNOWN', basis: '容器中间件未关联 DevOps 应用', candidates: [] };
const service = { name: 'Redis', confidence: 'EXACT', basis: '远端进程命令' };
const flow = {
  local: client, peer: published, resolution: 'EXACT', service,
  remoteProcesses: [{ serverIp: published.ip, pid: 500, startedAt: 'start', processName: 'Redis',
    application, service, confidence: 'EXACT', observedAt: 'observed',
    container: { id: 'a'.repeat(64), name: 'redis-custom', initPid: 400 },
    connection: { published, local: target, peer: client, socketId: 'backend' } }],
  dockerMappings: [{ containerId: 'a'.repeat(64), containerName: 'redis-custom', initPid: 400, published, target }],
  proxyProcesses: [{ pid: 600, startedAt: 'start', processName: 'docker-proxy-cu', observedAt: 'observed' }],
  networkActivity: 'UNKNOWN', sentDelta: 0, receivedDelta: null, samples: 52,
  configurationMatches: [], state: 'ESTAB', directionConfidence: 'INFERRED', directionBasis: '监听端口推断',
  reason: '容器内反向连接已匹配', firstSeenAt: 'first', lastSeenAt: 'last', warnings: [],
};

function render(value, overrides = {}, tab = 'details', job = null) {
  const report = { target: {}, coverage: { actualSeconds: 30, limitations: [] }, warnings: [], upstream: [], downstream: [value], ...overrides };
  const state = [30, { job, report: job?.status === 'running' ? null : report }, false, '', null, ''];
  let index = 0;
  const sandbox = { exports: {} };
  const tags = { Table: 'table', TableHeader: 'thead', TableBody: 'tbody', TableRow: 'tr', TableHead: 'th', TableCell: 'td' };
  sandbox.require = id => {
    if (id === 'react') return { ...React, useEffect: () => {}, useState: () => [state[index++], () => {}] };
    if (id === 'react/jsx-runtime') return require(id);
    if (id === '@/components/application-files') return { ApplicationFiles: () => null };
    if (id === '@/components/application-check-progress') return progressModule.exports;
    if (id === '@/components/ui/button') return { Button: ({ variant: _variant, ...props }) => React.createElement('button', props) };
    if (id === '@/components/ui/table') return Object.fromEntries(Object.entries(tags).map(([name, tag]) => [name, props => React.createElement(tag, props)]));
    if (id === '@/components/ui/tabs') return {
      Tabs: props => React.createElement(Primitive.Root, { ...props, defaultValue: tab }),
      TabsList: ({ variant: _variant, ...props }) => React.createElement(Primitive.List, props),
      TabsTrigger: Primitive.Tab,
      TabsContent: Primitive.Panel,
    };
    if (id === '@/lib/application-check') return checkModule.exports;
    if (id === '@/lib/datetime') return datetimeModule.exports;
    throw new Error(id);
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-check.tsx', 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, sandbox);
  return renderToStaticMarkup(React.createElement(sandbox.exports.ApplicationCheck, {
    asset: 'asset', ip: client.ip, pid: 123, name: 'orders',
  }));
}

test('Docker service shows container, translated endpoint and original client port independently of application', () => {
  const html = render(flow);
  for (const text of ['容器：redis-custom', 'Docker 端口映射', '10.0.0.2:7000/TCP', '172.18.0.6:6379/TCP',
    '10.0.0.1:33580/TCP', '容器内连接', '实际服务进程：500', 'Docker 代理：600', 'Redis', '未确认',
    '接收 未知 B', '52 次采样']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /疑似 Redis/);
});

test('running checks display live collection before a report exists and old jobs remain readable', () => {
  const html = render(flow, {}, 'details', { id: 'running', status: 'running', phase: '等待远端返回',
    startedAt: '2026-10-20T06:00:00Z', sourceVersion: '20261020-144403', duration: 60 });
  for (const text of ['实时采集', '等待远端返回', '环境识别中', '快照 2026-10-20 14:44:03',
    '旧任务仅提供阶段信息', '取消检测']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /下游连接 · 1 条连接/);
});

test('mapping without a matching connection stays inferred and never displays a backend owner', () => {
  const html = render({ ...flow, resolution: 'UNKNOWN', remoteProcesses: [], service: { ...service, confidence: 'INFERRED' } });
  assert.match(html, /疑似 Redis/);
  assert.match(html, /连接归属：未确认/);
  assert.match(html, /配置证据/);
  assert.doesNotMatch(html, /实际服务进程|容器内连接/);
});

test('older reports without Docker metadata still render', () => {
  const legacy = { ...flow, remoteProcesses: [], dockerMappings: undefined, proxyProcesses: undefined };
  const html = render(legacy);
  assert.match(html, /10.0.0.2:7000\/TCP/);
  assert.doesNotMatch(html, /Docker 端口映射|容器内连接|Docker 代理/);
});

test('listener candidates show PID and app in both tabs without becoming connection owners', () => {
  const listener = { ...flow.remoteProcesses[0], pid: 456, processName: 'Tomcat', confidence: 'INFERRED',
    container: undefined, connection: undefined, endpoint: endpoint('*', 8080), basis: '实时监听 Endpoint',
    application: { ...application, appId: '202', appName: 'billing', confidence: 'INFERRED' } };
  const value = { ...flow, resolution: 'UNKNOWN', remoteProcesses: [], peerListeningProcesses: [listener] };
  const original = JSON.stringify(value);
  for (const tab of ['details', 'aggregate']) {
    const html = render(value, {}, tab);
    for (const text of ['456 · Tomcat', '端口监听（推断）', '202 · billing', '连接归属：未确认']) assert.ok(html.includes(text), text);
    assert.doesNotMatch(html, /容器内连接|实际服务进程/);
  }
  assert.equal(JSON.stringify(value), original);
  const owner = { ...value, remoteProcesses: [listener] };
  const html = render(value, { downstream: [value, owner, { ...value, peerListeningProcesses: [] }] }, 'aggregate');
  assert.match(html, /下游连接 · 3 条聚合连接/);
  assert.equal((render(owner).match(/456 · Tomcat/g) || []).length, 2); // Process column plus listener evidence.
  assert.doesNotMatch(render(owner), /端口监听（推断）/);
});

test('partial reports show retained coverage and render each error only once', () => {
  const error = { stage: 'target_probe', code: 'SOCKET_PROBE_TIMEOUT', exitCode: null,
    message: '等待远端探针超时', diagnostics: ['终端等待结束标记超时'] };
  const html = render(flow, { coverage: { actualSeconds: 49.6, limitations: [] }, error,
    warnings: [error.message, error.message+'（阶段 target_probe）', ...error.diagnostics, '独立的对端警告'] });
  assert.match(html, /实际观察 49.6 秒/);
  assert.equal(html.split(error.message).length - 1, 1);
  assert.equal(html.split(error.diagnostics[0]).length - 1, 1);
  assert.match(html, /独立的对端警告/);
});

test('sampling coverage shows measured intervals and labels state as a past observation', () => {
  const html = render({ ...flow, state: 'UNKNOWN' }, {
    coverage: { actualSeconds: 30, limitations: [], samples: 55,
      averageIntervalSeconds: 0.548, maxIntervalSeconds: 1.2, requestedSamplePeriodSeconds: 0.2,
      peers: [{ serverIp: '10.0.0.2', samples: 100, averageIntervalSeconds: 0.209, maxIntervalSeconds: 0.3 }] },
  });
  for (const text of ['最近一次观察到的 TCP 状态：未确认', '方向依据', '对端进程归属',
    '目标有效采样 55 次', '平均观察间隔 0.548 秒', '最长 1.2 秒', '远端目标采样间隔 0.2 秒',
    '对端 10.0.0.2：有效采样 100 次']) assert.ok(html.includes(text), text);
});

test('connection details remain the default tab with all original columns', () => {
  const html = render(flow);
  assert.match(html, /role="tablist"[^>]*aria-label="上下游连接展示"/);
  assert.match(html, /aria-selected="true"[^>]*>连接明细/);
  assert.match(html, /aria-selected="false"[^>]*>连接聚合/);
  assert.match(html, /role="tabpanel"/);
  assert.match(html, /下游连接 · 1 条连接/);
  for (const text of ['对端 IP / 端口', '网络活动', '配置核对 / 连接证据', '查看连接证据']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /条聚合连接/);
  assert.match(html, /全部已发现且能唯一匹配资产的远端服务器/);
  assert.doesNotMatch(html, /最多 4 台远端服务器|远端名额/);
});

test('resolved peer app ID and name render in both directions and both connection tabs', () => {
  const peer = { ...flow, remoteProcesses: [{ ...flow.remoteProcesses[0], pid: 32453, processName: 'Tomcat',
    container: undefined, connection: undefined,
    application: { appId: '1508', appName: 'phoenix-product', confidence: 'INFERRED', basis: 'Tomcat 实例与主机部署匹配', candidates: [] } }] };
  for (const tab of ['details', 'aggregate']) {
    const html = render(peer, { upstream: [peer] }, tab);
    assert.equal((html.match(/32453 · Tomcat/g) || []).length, 2);
    assert.equal((html.match(/1508 · phoenix-product/g) || []).length, 2);
  }
});

test('aggregation ignores connection ports, keeps directions separate and displays only four columns', () => {
  const duplicate = { ...flow, peer: endpoint(published.ip, 7001), local: endpoint(client.ip, 33581) };
  const html = render(flow, { upstream: [flow, duplicate], downstream: [flow, duplicate, flow] }, 'aggregate');
  assert.match(html, /aria-selected="true"[^>]*>连接聚合/);
  assert.match(html, /上游连接 · 1 条聚合连接/);
  assert.match(html, /下游连接 · 1 条聚合连接/);
  const tables = html.match(/<table>[\s\S]*?<\/table>/g);
  assert.equal(tables.length, 2);
  for (const table of tables) {
    assert.deepEqual([...table.matchAll(/<th>(.*?)<\/th>/g)].map(match => match[1]),
      ['对端 IP', '类型', '对端进程 PID / 名称', 'DevOps 应用 ID / 名称']);
    assert.equal((table.match(/<tr>/g) || []).length, 2);
    assert.equal((table.match(/500 · Redis/g) || []).length, 1);
    assert.match(table, /<code>10\.0\.0\.2<\/code>/);
  }
  assert.doesNotMatch(html, /网络活动|配置核对|查看连接证据|52 次采样|10\.0\.0\.2:700[01]/);
});

test('aggregation separates different peer IPs, PIDs and process names', () => {
  const remote = flow.remoteProcesses[0];
  const html = render(flow, { downstream: [flow,
    { ...flow, peer: endpoint('10.0.0.3', 7000), remoteProcesses: [{ ...remote, serverIp: '10.0.0.3' }] },
    { ...flow, remoteProcesses: [{ ...remote, pid: 501 }] },
    { ...flow, remoteProcesses: [{ ...remote, processName: 'redis-server' }] },
    { ...flow, remoteProcesses: [{ ...remote, startedAt: 'restarted' }] },
  ] }, 'aggregate');
  assert.match(html, /下游连接 · 4 条聚合连接/);
  for (const text of ['10.0.0.3', '501 · Redis', '500 · redis-server']) assert.ok(html.includes(text), text);
});

test('aggregation preserves unresolved rows and ambiguous candidate sets regardless of their order', () => {
  const remote = flow.remoteProcesses[0];
  const candidate = { ...remote, pid: 501, processName: 'other-worker' };
  const html = render(flow, { downstream: [flow,
    { ...flow, resolution: 'UNKNOWN', remoteProcesses: [] },
    { ...flow, peer: endpoint(published.ip, 7001), resolution: 'UNKNOWN', remoteProcesses: [] },
    { ...flow, resolution: 'AMBIGUOUS', remoteProcesses: [remote, candidate] },
    { ...flow, resolution: 'AMBIGUOUS', remoteProcesses: [candidate, remote] },
  ] }, 'aggregate');
  assert.match(html, /下游连接 · 3 条聚合连接/);
  for (const text of ['连接归属：精确', '连接归属：未确认', '连接归属：多候选', '501 · other-worker']) assert.ok(html.includes(text), text);
  assert.match(render(flow, { upstream: [], downstream: [] }, 'aggregate'), /下游连接 · 0 条聚合连接/);
});

test('aggregation retains distinct service and application conclusions without changing the report', () => {
  const remote = flow.remoteProcesses[0];
  const knownApp = { appId: '101', appName: 'orders', confidence: 'EXACT', basis: '部署匹配', candidates: [] };
  const ambiguousApp = { ...application, confidence: 'AMBIGUOUS', candidates: [
    { appId: '102', appName: 'worker', confidence: 'INFERRED', basis: '名称匹配' },
    { appId: '103', appName: 'consumer', confidence: 'INFERRED', basis: '名称匹配' },
  ] };
  const downstream = [flow,
    { ...flow, resolution: 'INFERRED', service: { ...service, name: 'HTTP', confidence: 'INFERRED' },
      remoteProcesses: [{ ...remote, application: knownApp }] },
    { ...flow, remoteProcesses: [{ ...remote, application: ambiguousApp }] },
  ];
  const original = JSON.stringify(downstream);
  const html = render(flow, { downstream }, 'aggregate');
  assert.match(html, /下游连接 · 1 条聚合连接/);
  for (const text of ['疑似 HTTP', 'Redis', '101 · orders', '多候选：102 · worker；103 · consumer', '连接归属：精确 / 推断', '未确认']) assert.ok(html.includes(text), text);
  assert.equal(JSON.stringify(downstream), original);
});
