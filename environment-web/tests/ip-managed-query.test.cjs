const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function evaluateTs(file, requireModule = () => {}) {
  const source = fs.readFileSync(file, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      esModuleInterop: true,
    },
  }).outputText;
  const sandbox = { exports: {}, require: requireModule };
  vm.runInNewContext(output, sandbox);
  return sandbox.exports;
}

// 真实的 server-processes（无外部依赖），保证类型判定与生产一致。
const serverProcesses = evaluateTs(
  path.join(__dirname, '../lib/server-processes.ts'),
);

// 真实的 datacenter-networks（无外部依赖），保证归属判定与生产一致。
const datacenterNetworks = evaluateTs(
  path.join(__dirname, '../lib/datacenter-networks.ts'),
);

function formatBeijingTime(value) {
  if (!value) return '';
  const ts0 = Date.parse(
    /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
      ? value.replace(' ', 'T') + '+08:00'
      : value,
  );
  if (!Number.isFinite(ts0)) return '';
  const d = new Date(ts0 + 8 * 3600000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

function loadModule(jumpserverStub) {
  return evaluateTs(
    path.join(__dirname, '../lib/ip-managed-query.ts'),
    (id) => {
      if (id === '@/lib/jumpserver') return jumpserverStub || { jumpserver: { assets: [] } };
      if (id === '@/lib/server-processes') return serverProcesses;
      if (id === '@/lib/datacenter-networks') return datacenterNetworks;
      if (id === '@/lib/inventory')
        return {
          envs: ['TEST', 'SIMULATION', 'PRODUCT'],
          shortLabels: { TEST: '测试', SIMULATION: '仿真', PRODUCT: '生产' },
          latestSuccessfulPushIn: () => '',
        };
      if (id === '@/lib/process-comparison')
        return {
          ipServerState: () => 'can_login',
          ipSpecSummary: () => '',
          processesForIp: () => [],
          webappsForIp: () => [],
          matchAppToProcess: () => undefined,
          matchAppToWebapp: () => undefined,
          ipDeployEnvironments: () => [],
          appDeployEnvironments: (app, ip) =>
            ['TEST', 'SIMULATION', 'PRODUCT'].filter((e) =>
              (app.envs[e] || []).some((r) => (r.ip || '').trim() === ip),
            ),
          formatBeijingTime,
        };
      throw new Error(id);
    },
  );
}

const lib = loadModule({ jumpserver: { assets: [] } });

const DASH = '-';

function proc(partial) {
  return {
    pid: 1000,
    ppid: 1,
    user: 'app',
    name: 'x',
    kind: 'x',
    startedAt: '',
    elapsedSeconds: 60,
    command: '',
    ...partial,
  };
}

// 注入式依赖：匹配规则刻意简单（命令行包含应用名 / 目录名等于应用名），
// 让测试聚焦于行构建、中间件裁剪与时间合并逻辑。
function deps(
  { states = {}, specsByIp = {}, hostNamesByIp = {}, procsByIp = {}, webappsByIp = {}, pushByApp = {} } = {},
) {
  return {
    state: (ip) => states[ip] || 'can_login',
    spec: (ip) => specsByIp[ip] || '',
    hostName: (ip) => hostNamesByIp[ip] || ip,
    processes: (ip) => procsByIp[ip] || [],
    webapps: (ip) => webappsByIp[ip] || [],
    matchProcess: (app, ps) => ps.find((p) => p.command.includes(app.name)),
    matchWebapp: (appName, ws) => ws.find((w) => w.name === appName),
    appPushIn: (app) => pushByApp[app.id] || '',
    // 测试桩：取最新有效时间但不重新格式化。
    latestTime: (values) => {
      const valid = values.filter((v) => v && Number.isFinite(Date.parse(v)));
      if (!valid.length) return '';
      return valid.sort((a, b) => Date.parse(b) - Date.parse(a))[0];
    },
  };
}

const app = (over) => ({
  id: '101',
  name: 'user-center',
  appType: 'app',
  envs: { TEST: [{ ip: '10.58.0.10' }], SIMULATION: [], PRODUCT: [] },
  ...over,
});

test('未纳管 IP 输出单行，全部列以 - 占位', () => {
  const rows = lib.buildIpManagedRows(
    ['10.58.0.99'],
    [],
    undefined,
    deps({ states: { '10.58.0.99': 'not_collected' } }),
  );
  assert.equal(rows.length, 1);
  // vm 沙箱对象原型不同，逐字段比较。
  const row = rows[0];
  assert.equal(row.ip, '10.58.0.99');
  assert.equal(row.managed, '未纳管');
  assert.equal(row.source, 'unmanaged');
  assert.equal(row.spec, DASH);
  assert.equal(row.appType, DASH);
  assert.equal(row.detail, DASH);
  assert.equal(row.appId, DASH);
  assert.equal(row.appName, DASH);
  assert.equal(row.pushIn, DASH);
});

test('已纳管但无法登录的 IP 单行标注无法登录，未采集', () => {
  const rows = lib.buildIpManagedRows(
    ['10.58.0.2'],
    [],
    undefined,
    deps({
      states: { '10.58.0.2': 'cannot_login' },
      procsByIp: { '10.58.0.2': [proc({ name: 'java', command: 'java -jar a.jar' })] },
    }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].managed, '已纳管');
  assert.equal(rows[0].detail, '无法登录，未采集');
  assert.equal(rows[0].appId, DASH);
});

test('业务进程各占一行并关联 DevOps 应用，Push In 取进程启动与流水线最新者', () => {
  const javaProc = proc({
    pid: 384,
    name: 'java',
    startedAt: '2026-09-28 10:00:00',
    command: 'java -jar /opt/user-center-exec.jar --server.port=7084',
  });
  const redisProc = proc({
    pid: 921,
    name: 'redis-server',
    command: 'redis-server 0.0.0.0:6379',
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [app()],
    undefined,
    deps({
      procsByIp: { '10.58.0.10': [redisProc, javaProc] },
      pushByApp: { '101': '2026-09-01 09:00:00' },
    }),
  );
  // 按 PID 排序：redis(921) 在 java(384) 之后
  assert.equal(rows.length, 2);
  const [javaRow, redisRow] = rows;
  assert.equal(javaRow.source, 'process');
  assert.equal(javaRow.appType, 'Java');
  assert.equal(javaRow.detail, 'user-center-exec.jar');
  assert.equal(javaRow.appId, '101');
  assert.equal(javaRow.appName, 'user-center');
  assert.equal(javaRow.pushIn, '2026-09-28 10:00:00');
  assert.equal(redisRow.appType, 'Redis');
  assert.equal(redisRow.detail, 'redis-server (PID 921)');
  assert.equal(redisRow.appId, DASH);
  assert.equal(redisRow.appName, DASH);
  assert.equal(redisRow.pushIn, DASH);
});

test('Tomcat 行使用实例目录，命中部署应用时关联应用ID/名称/Push In', () => {
  const tomcat = proc({
    pid: 500,
    name: 'java',
    startedAt: '2026-09-28 10:00:00',
    command:
      'java -Dcatalina.base=/opt/apache-tomcat_merchant_93905 org.apache.catalina.startup.Bootstrap start',
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [app({ id: '202', name: 'merchant' })],
    undefined,
    deps({
      procsByIp: { '10.58.0.10': [tomcat] },
      pushByApp: { '202': '2026-09-01 09:00:00' },
    }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].appType, 'Tomcat');
  assert.equal(rows[0].detail, '/opt/apache-tomcat_merchant_93905');
  assert.equal(rows[0].appId, '202');
  assert.equal(rows[0].appName, 'merchant');
  assert.equal(rows[0].pushIn, '2026-09-28 10:00:00');
});

test('Tomcat 实例目录未命中部署应用时回退为 -', () => {
  const tomcat = proc({
    pid: 501,
    name: 'java',
    command:
      'java -Dcatalina.base=/opt/apache-tomcat_unknown-app_93905 org.apache.catalina.startup.Bootstrap start',
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [app({ id: '202', name: 'merchant' })],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [tomcat] } }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].appType, 'Tomcat');
  assert.equal(rows[0].appId, DASH);
  assert.equal(rows[0].appName, DASH);
});

test('Kafka 与 RocketMQ Java 进程归类为 MQ 中间件', () => {
  const kafka = proc({ pid: 701, name: 'java', command: 'java kafka.Kafka server.properties' });
  const rocketmq = proc({
    pid: 702,
    name: 'java',
    command: 'java org.apache.rocketmq.broker.BrokerStartup -c broker.conf',
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [kafka, rocketmq] } }),
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].appType, 'MQ');
  assert.equal(rows[1].appType, 'MQ');
  assert.ok(rows.every((r) => r.appId === DASH));
});

test('新识别的共享组件保留类型且不关联同名业务应用', () => {
  const processes = [
    proc({ pid: 1, name: 'java', command: 'java org.elasticsearch.bootstrap.Elasticsearch' }),
    proc({ pid: 2, name: 'java', command: 'java org.apache.zookeeper.server.quorum.QuorumPeerMain zoo.cfg' }),
    proc({ pid: 3, name: 'java', command: 'java -jar nacos-server.jar' }),
    proc({ pid: 4, name: 'java', command: 'java com.alibaba.otter.canal.deployer.CanalLauncher' }),
    proc({ pid: 5, name: 'beam.smp', command: 'beam.smp -s rabbit boot' }),
  ];
  const rows = lib.buildIpManagedRows(['10.58.0.10'], [app({name:'java'})], undefined,
    deps({procsByIp:{'10.58.0.10':processes}}));
  assert.equal(rows.map(r=>r.appType).join(','), 'Elasticsearch,ZooKeeper,Nacos,Canal,MQ');
  assert.ok(rows.every(r=>r.appId===DASH));
});

test('node 进程详细取脚本路径并附带 PID', () => {
  const node = proc({
    pid: 810,
    name: 'node',
    command: 'node /srv/gateway/server.js --port 3000',
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [node] } }),
  );
  assert.equal(rows[0].appType, 'Node');
  assert.equal(rows[0].detail, '/srv/gateway/server.js (PID 810)');
});

test('系统进程保留导出并标注为系统进程，不关联 DevOps 应用', () => {
  const sshd = proc({ pid: 900, name: 'sshd', command: '/usr/sbin/sshd -D', system: true });
  const kthread = proc({ pid: 2, name: 'kthreadd', command: '[kthreadd]', system: true });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [sshd, kthread] } }),
  );
  assert.equal(rows.length, 2);
  assert.ok(rows.every((r) => r.appType === '系统进程'));
  // 进程行按 PID 升序排列。
  assert.equal(rows[0].detail, 'kthreadd (PID 2)');
  assert.equal(rows[1].detail, 'sshd (PID 900)');
  assert.ok(rows.every((r) => r.appId === DASH && r.appName === DASH && r.pushIn === DASH));
});

test('同一 jar 多实例时重名详细追加 PID 区分', () => {
  const a = proc({ pid: 111, name: 'java', command: 'java -jar /opt/worker.jar' });
  const b = proc({ pid: 222, name: 'java', command: 'java -jar /opt/worker.jar --profile=2' });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [a, b] } }),
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].detail, 'worker.jar (PID 111)');
  assert.equal(rows[1].detail, 'worker.jar (PID 222)');
});

test('webapps 目录各一行：命中应用带 appType/应用信息，未命中归其它', () => {
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [app({ id: '303', name: 'tc-fostay', appType: 'node-backend' })],
    undefined,
    deps({
      procsByIp: { '10.58.0.10': [] },
      webappsByIp: {
        '10.58.0.10': [
          { name: 'zzz-other', mtime: '2026-09-10 08:00:00' },
          { name: 'tc-fostay', mtime: '2026-09-20 08:00:00' },
        ],
      },
      pushByApp: { '303': '2026-09-01 08:00:00' },
    }),
  );
  assert.equal(rows.length, 2);
  const [hit, miss] = rows; // 按目录名排序
  assert.equal(hit.appType, 'node-backend');
  assert.equal(hit.detail, '/var/www/webapps/tc-fostay');
  assert.equal(hit.appId, '303');
  assert.equal(hit.appName, 'tc-fostay');
  assert.equal(hit.pushIn, '2026-09-20 08:00:00');
  assert.equal(miss.detail, '/var/www/webapps/zzz-other');
  assert.equal(miss.appType, '其它');
  assert.equal(miss.appId, DASH);
});

test('Nginx 进程不出行，每个原始配置文件一行', () => {
  const master = proc({
    pid: 900,
    name: 'nginx',
    command: 'nginx: master process /usr/sbin/nginx -c /etc/nginx/nginx.conf',
  });
  const worker = proc({ pid: 901, name: 'nginx', command: 'nginx: worker process' });
  const nginxByIp = new Map([
    [
      '10.58.0.10',
      {
        files: [
          { path: '/etc/nginx/conf.d/b.conf' },
          { path: '/etc/nginx/nginx.conf' },
        ],
      },
    ],
  ]);
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    nginxByIp,
    deps({ procsByIp: { '10.58.0.10': [master, worker] } }),
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].detail, '/etc/nginx/conf.d/b.conf');
  assert.equal(rows[1].detail, '/etc/nginx/nginx.conf');
  assert.ok(rows.every((r) => r.appType === 'Nginx' && r.appId === DASH));
});

test('配置未采集但有 Nginx 进程时回退一条主进程行', () => {
  const master = proc({
    pid: 900,
    name: 'nginx',
    command: 'nginx: master process /usr/sbin/nginx',
  });
  const worker = proc({ pid: 901, ppid: 900, name: 'nginx', command: 'nginx: worker process' });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [worker, master] } }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].source, 'nginx');
  assert.equal(rows[0].detail, 'nginx 主进程 (PID 900)，配置未采集');
});

test('配置拉取失败时主进程行附带失败说明', () => {
  const master = proc({
    pid: 900,
    name: 'nginx',
    command: 'nginx: master process /usr/sbin/nginx',
  });
  const nginxByIp = new Map([['10.58.0.10', { files: [], error: '该版本未保存此资产的 Nginx 原始配置' }]]);
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    nginxByIp,
    deps({ procsByIp: { '10.58.0.10': [master] } }),
  );
  assert.equal(rows.length, 1);
  assert.match(rows[0].detail, /原始配置读取失败/);
});

test('可登录但无任何进程/目录/配置时单行兜底', () => {
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [app()],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [] }, webappsByIp: { '10.58.0.10': [] } }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].source, 'empty');
  assert.equal(rows[0].managed, '已纳管');
});

test('多 IP 行顺序与输入一致，且互不串数据', () => {
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10', '10.58.0.99'],
    [app()],
    undefined,
    deps({
      states: { '10.58.0.99': 'not_collected' },
      procsByIp: {
        '10.58.0.10': [proc({ name: 'redis-server', pid: 1, command: 'redis-server' })],
      },
    }),
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].ip, '10.58.0.10');
  assert.equal(rows[1].ip, '10.58.0.99');
});

test('服务器规格在同一 IP 的所有行上一致，无规格数据回退 -，IP 间不串', () => {
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10', '10.58.0.2', '10.58.0.99'],
    [],
    undefined,
    deps({
      states: { '10.58.0.2': 'cannot_login', '10.58.0.99': 'not_collected' },
      specsByIp: { '10.58.0.10': '4C 64G' },
      procsByIp: {
        '10.58.0.10': [
          proc({ pid: 1, name: 'java', command: 'java -jar /opt/a.jar' }),
          proc({ pid: 2, name: 'redis-server', command: 'redis-server' }),
        ],
      },
    }),
  );
  const [javaRow, redisRow, unreachableRow, unmanagedRow] = rows;
  assert.equal(javaRow.spec, '4C 64G');
  assert.equal(redisRow.spec, '4C 64G');
  assert.equal(unreachableRow.detail, '无法登录，未采集');
  assert.equal(unreachableRow.spec, DASH);
  assert.equal(unmanagedRow.managed, '未纳管');
  assert.equal(unmanagedRow.spec, DASH);
});

test('每行携带服务器名称、DevOps 环境与资源归属，名称缺省回退 IP', () => {
  const localIp = '10.58.9.217';
  const cloudIp = '10.179.1.224';
  const deployedApp = app({
    envs: {
      TEST: [{ ip: localIp }],
      SIMULATION: [],
      PRODUCT: [{ ip: localIp }],
    },
  });
  const rows = lib.buildIpManagedRows(
    [localIp, cloudIp],
    [deployedApp],
    undefined,
    deps({
      states: { [cloudIp]: 'not_collected' },
      hostNamesByIp: { [localIp]: 'pay-host-01' },
      procsByIp: {
        [localIp]: [proc({ name: 'java', command: 'java -jar user-center.jar' })],
      },
    }),
  );
  assert.equal(rows[0].hostName, 'pay-host-01');
  assert.deepEqual(rows[0].deployEnvs, ['TEST', 'PRODUCT']);
  // 映射到 DevOps 应用的进程：appEnvs 为该应用在此 IP 的环境。
  assert.deepEqual(rows[0].appEnvs, ['TEST', 'PRODUCT']);
  assert.equal(rows[0].owner, '本地机房');
  // 未纳管 IP：名称回退 IP、无环境、非本地网段归属阿里云ECS。
  assert.equal(rows[1].hostName, cloudIp);
  assert.deepEqual(rows[1].deployEnvs, []);
  assert.equal(rows[1].owner, '阿里云ECS');
});

test('formatDeployEnvs 用顿号连接短环境名，无环境为 -', () => {
  assert.equal(lib.formatDeployEnvs(['TEST', 'PRODUCT']), '测试、生产');
  assert.equal(lib.formatDeployEnvs(['SIMULATION']), '仿真');
  assert.equal(lib.formatDeployEnvs([]), '-');
  assert.equal(lib.formatDeployEnvs(undefined), '-');
});

test('latestFormattedTime 按北京时间格式化并取最新候选', () => {
  assert.equal(
    lib.latestFormattedTime(['2026-09-30T03:24:05+00:00', '2026-09-29 10:00:00']),
    '2026-09-30 11:24:05',
  );
  assert.equal(lib.latestFormattedTime(['', 'bad']), '');
});

test('进程行携带内存占用率与实际内存，旧快照缺字段时展示 -', () => {
  const withMem = proc({
    pid: 384,
    name: 'java',
    command: 'java -jar /opt/user-center-exec.jar --server.port=7084',
    memPercent: 12.34,
    rssKB: 1048576,
    vszKB: 4194304,
  });
  const withoutMem = proc({
    pid: 385,
    name: 'java',
    command: 'java -jar /opt/legacy.jar',
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [withMem, withoutMem] } }),
  );
  assert.equal(rows[0].memPercent, 12.34);
  assert.equal(rows[0].rssKB, 1048576);
  assert.equal(rows[1].memPercent, undefined);
  assert.equal(lib.formatMemPercent(12.34), '12.3%');
  assert.equal(lib.formatMemPercent(undefined), '-');
  assert.equal(lib.formatMemorySize(600), '600 KB');
  assert.equal(lib.formatMemorySize(51200), '50 MB');
  assert.equal(lib.formatMemorySize(1048576), '1.00 GB');
  assert.equal(lib.formatMemorySize(undefined), '-');
});

test('CSV 表头含内存两列，非进程行内存为 -', () => {
  const nginxByIp = new Map([
    [
      '10.58.0.10',
      { files: [{ path: '/etc/nginx/conf.d/a,b.conf' }] },
    ],
  ]);
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    nginxByIp,
    deps({ procsByIp: { '10.58.0.10': [] }, webappsByIp: {} }),
  );
  const csv = lib.managedRowsCsv(rows).split('\r\n');
  assert.equal(
    csv[0],
    'IP,服务器名称,环境,资源归属,是否纳管,服务器规格,应用类型,进程ID,进程/应用详细,进程命令,监听端口,内存占用率,实际内存,DevOps应用ID,DevOps应用名称,最新Push In时间',
  );
  assert.equal(
    csv[1],
    '10.58.0.10,10.58.0.10,,阿里云ECS,已纳管,-,Nginx,-,"/etc/nginx/conf.d/a,b.conf",-,-,-,-,-,-,-',
  );
});

test('CSV 进程行输出内存占用率与实际内存', () => {
  const javaProc = proc({
    pid: 384,
    name: 'java',
    command: 'java -jar /opt/user-center-exec.jar --server.port=7084',
    memPercent: 12.34,
    rssKB: 1048576,
    ports: [7084],
  });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({
      specsByIp: { '10.58.0.10': '4C 64G' },
      procsByIp: { '10.58.0.10': [javaProc] },
    }),
  );
  const csv = lib.managedRowsCsv(rows).split('\r\n');
  assert.equal(
    csv[1],
    '10.58.0.10,10.58.0.10,,阿里云ECS,已纳管,4C 64G,Java,384,user-center-exec.jar,java -jar /opt/user-center-exec.jar --server.port=7084,7084,12.3%,1.00 GB,-,-,-',
  );
});

test('进程行携带监听端口，非进程行与旧快照展示 -', () => {
  const java = proc({
    pid: 384,
    name: 'java',
    command: 'java -jar /opt/a.jar',
    ports: [8080, 7084],
  });
  const silent = proc({ pid: 385, name: 'redis-server', command: 'redis-server' });
  const rows = lib.buildIpManagedRows(
    ['10.58.0.10'],
    [],
    undefined,
    deps({ procsByIp: { '10.58.0.10': [java, silent] } }),
  );
  assert.deepEqual([...rows[0].ports].sort((a, b) => a - b), [7084, 8080]);
  assert.equal(rows[1].ports, undefined);
  assert.equal(lib.formatPorts([80, 443]), '80, 443');
  assert.equal(lib.formatPorts([]), '-');
  assert.equal(lib.formatPorts(undefined), '-');
  const csv = lib.managedRowsCsv(rows).split('\r\n');
  assert.equal(
    csv[1],
    '10.58.0.10,10.58.0.10,,阿里云ECS,已纳管,-,Java,384,a.jar,java -jar /opt/a.jar,"8080, 7084",-,-,-,-,-',
  );
  assert.equal(csv[2], '10.58.0.10,10.58.0.10,,阿里云ECS,已纳管,-,Redis,385,redis-server (PID 385),redis-server,-,-,-,-,-,-');
});

test('进程环境取自其映射的 DevOps 应用，未映射进程环境为空', () => {
  const mappedApp = app({
    id: '1656',
    name: 'phoenix-mappingtool',
    envs: { TEST: [{ ip: '10.179.1.224' }], SIMULATION: [], PRODUCT: [] },
  });
  const redis = proc({ pid: 1, name: 'redis-server', command: 'redis-server' });
  const java = proc({
    pid: 2,
    name: 'java',
    command: 'java -jar phoenix-mappingtool.jar --server.port=37863',
  });
  const rows = lib.buildIpManagedRows(
    ['10.179.1.224'],
    [mappedApp],
    undefined,
    deps({ procsByIp: { '10.179.1.224': [redis, java] } }),
  );
  // PID 升序：redis(1) 在前、java(2) 在后。
  assert.equal(rows[0].appId, DASH);
  assert.equal(rows[0].appEnvs, undefined);
  assert.equal(rows[1].appId, '1656');
  assert.deepEqual(rows[1].appEnvs, ['TEST']);
  const csv = lib.managedRowsCsv(rows).split('\r\n');
  assert.equal(
    csv[1],
    '10.179.1.224,10.179.1.224,,阿里云ECS,已纳管,-,Redis,1,redis-server (PID 1),redis-server,-,-,-,-,-,-',
  );
  assert.equal(
    csv[2],
    '10.179.1.224,10.179.1.224,测试,阿里云ECS,已纳管,-,Java,2,phoenix-mappingtool.jar,java -jar phoenix-mappingtool.jar --server.port=37863,-,-,-,1656,phoenix-mappingtool,-',
  );
});

test('nginxConfigRequest 仅对可登录且已采集配置的资产返回版本参数', () => {
  const mod = loadModule({
    jumpserver: {
      assets: [
        {
          id: 'a1',
          ip: '10.0.0.1',
          inspection: {
            loginStatus: 'can_login',
            configurationCount: 2,
            configurationVersion: '20260930-01',
          },
        },
        {
          id: 'a2',
          ip: '10.0.0.2',
          inspection: {
            loginStatus: 'cannot_login',
            configurationCount: 5,
            configurationVersion: '20260930-02',
          },
        },
        {
          id: 'a3',
          ip: '10.0.0.3',
          inspection: { loginStatus: 'can_login', configurationCount: 0 },
        },
      ],
    },
  });
  const req = mod.nginxConfigRequest('10.0.0.1');
  assert.equal(req.asset, 'a1');
  assert.equal(req.version, '20260930-01');
  assert.equal(mod.nginxConfigRequest('10.0.0.2'), undefined);
  assert.equal(mod.nginxConfigRequest('10.0.0.3'), undefined);
  assert.equal(mod.nginxConfigRequest('10.0.0.9'), undefined);
});

// ─── 按IP汇总测试 ───

function summaryDeps({
  states = {},
  specsByIp = {},
  hostNamesByIp = {},
  envsByIp = {},
  procsByIp = {},
  usageByIp = {},
  appPortsByIp = {},
} = {}) {
  return {
    state: (ip) => states[ip] || 'can_login',
    spec: (ip) => specsByIp[ip] || '',
    hostName: (ip) => hostNamesByIp[ip] || ip,
    deployEnvs: (ip) => envsByIp[ip] || [],
    processes: (ip) => procsByIp[ip] || [],
    serverUsage: (ip) => usageByIp[ip],
    appPorts: (ip) => appPortsByIp[ip],
  };
}

test('汇总-未纳管IP仅显示未纳管与规格，无CPU/内存/端口/分类', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.99'],
    summaryDeps({
      states: { '10.58.0.99': 'not_collected' },
      specsByIp: { '10.58.0.99': '2C 4G' },
    }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].ip, '10.58.0.99');
  assert.equal(rows[0].managed, '未纳管');
  assert.equal(rows[0].spec, '2C 4G');
  assert.equal(rows[0].cpuPercent, undefined);
  assert.equal(rows[0].memPercent, undefined);
  assert.equal(rows[0].portCount, null);
  assert.equal(rows[0].categories.length, 0);
  assert.equal(rows[0].portList.length, 0);
});

test('汇总-已纳管但无法登录的IP无进程数据', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.5'],
    summaryDeps({
      states: { '10.58.0.5': 'cannot_login' },
      specsByIp: { '10.58.0.5': '4C 8G' },
    }),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].managed, '已纳管');
  assert.equal(rows[0].spec, '4C 8G');
  assert.equal(rows[0].cpuPercent, undefined);
  assert.equal(rows[0].categories.length, 0);
});

test('汇总-可登录IP显示CPU内存端口数与进程分类', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.10'],
    summaryDeps({
      states: { '10.58.0.10': 'can_login' },
      specsByIp: { '10.58.0.10': '8C 16G' },
      procsByIp: {
        '10.58.0.10': [
          proc({ pid: 100, name: 'java', command: 'java -jar user-center.jar -Dserver.port=8080' }),
          proc({ pid: 101, name: 'java', command: 'java -jar order-api.jar' }),
          proc({ pid: 200, name: 'nginx', command: 'nginx: master process /usr/sbin/nginx' }),
          proc({ pid: 201, name: 'nginx', command: 'nginx: worker process' }),
          proc({ pid: 202, name: 'nginx', command: 'nginx: worker process' }),
        ],
      },
      usageByIp: { '10.58.0.10': { cpuPercent: 45, memPercent: 62, memTotalMB: 16384, memUsedMB: 10158 } },
      appPortsByIp: {
        '10.58.0.10': [
          { app: 'user-center', kind: 'java', port: 8080, addresses: ['*'], pids: [100] },
          { app: 'nginx', kind: 'nginx', port: 80, addresses: ['*'], pids: [200, 201, 202] },
        ],
      },
    }),
  );
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.managed, '已纳管');
  assert.equal(row.spec, '8C 16G');
  assert.equal(row.cpuPercent, 45);
  assert.equal(row.memPercent, 62);
  assert.equal(row.portCount, 2);
  assert.equal(row.portList.length, 2);
  assert.equal(row.portList[0], 80);
  assert.equal(row.portList[1], 8080);

  const types = Object.fromEntries(row.categories);
  assert.equal(types['Java Jar'], 2);
  assert.equal(types['Nginx'], 1);
  assert.equal(types['Tomcat'], undefined);
});

test('汇总-每行携带名称、环境与资源归属，名称缺省回退 IP', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.10.5', '10.58.0.99'],
    summaryDeps({
      states: { '10.58.0.99': 'not_collected' },
      hostNamesByIp: { '10.58.10.5': 'dc-core-05' },
      envsByIp: { '10.58.10.5': ['TEST', 'SIMULATION', 'PRODUCT'] },
    }),
  );
  assert.equal(rows[0].hostName, 'dc-core-05');
  assert.deepEqual(rows[0].deployEnvs, ['TEST', 'SIMULATION', 'PRODUCT']);
  assert.equal(rows[0].owner, '本地机房');
  // 未纳管行同样携带名称（回退 IP）与归属。
  assert.equal(rows[1].hostName, '10.58.0.99');
  assert.deepEqual(rows[1].deployEnvs, []);
  assert.equal(rows[1].owner, '阿里云ECS');
});

test('汇总-旧快照无appPorts时回退进程端口并集', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.20'],
    summaryDeps({
      procsByIp: {
        '10.58.0.20': [
          proc({ pid: 1, name: 'java', command: 'java -jar app.jar', ports: [8080, 9090] }),
          proc({ pid: 2, name: 'redis-server', command: 'redis-server', ports: [6379] }),
        ],
      },
      appPortsByIp: {}, // 无 appPorts
    }),
  );
  assert.equal(rows[0].portCount, 3);
  assert.equal(rows[0].portList.length, 3);
  assert.equal(rows[0].portList[0], 6379);
  assert.equal(rows[0].portList[1], 8080);
  assert.equal(rows[0].portList[2], 9090);
});

test('汇总-无端口数据时portCount为null', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.30'],
    summaryDeps({
      procsByIp: {
        '10.58.0.30': [
          proc({ pid: 1, name: 'java', command: 'java -jar app.jar' }), // 无 ports 字段
        ],
      },
    }),
  );
  assert.equal(rows[0].portCount, null);
  assert.equal(rows[0].portList.length, 0);
});

test('汇总-系统进程不纳入分类', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.40'],
    summaryDeps({
      procsByIp: {
        '10.58.0.40': [
          proc({ pid: 1, name: 'java', command: 'java -jar app.jar' }),
          proc({ pid: 2, name: 'kthreadd', command: 'kthreadd', system: true }),
          proc({ pid: 3, name: 'systemd', command: '/usr/lib/systemd/systemd', system: true }),
        ],
      },
    }),
  );
  const types = Object.fromEntries(rows[0].categories);
  assert.equal(types['Java Jar'], 1);
  assert.equal(types['系统进程'], undefined);
});

test('汇总-多IP行顺序与输入一致', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.10', '10.58.0.20', '10.58.0.30'],
    summaryDeps({
      states: {
        '10.58.0.10': 'can_login',
        '10.58.0.20': 'not_collected',
        '10.58.0.30': 'cannot_login',
      },
    }),
  );
  assert.equal(rows.length, 3);
  assert.equal(rows[0].ip, '10.58.0.10');
  assert.equal(rows[1].ip, '10.58.0.20');
  assert.equal(rows[2].ip, '10.58.0.30');
});

test('汇总-CSV输出包含所有列且格式正确', () => {
  const rows = lib.buildIpSummaryRows(
    ['10.58.0.10', '10.58.0.99'],
    summaryDeps({
      states: { '10.58.0.10': 'can_login', '10.58.0.99': 'not_collected' },
      specsByIp: { '10.58.0.10': '8C 16G' },
      procsByIp: {
        '10.58.0.10': [
          proc({ pid: 1, name: 'java', command: 'java -jar app.jar', ports: [8080] }),
          proc({ pid: 2, name: 'nginx', command: 'nginx: master process nginx', ports: [80, 443] }),
        ],
      },
      usageByIp: { '10.58.0.10': { cpuPercent: 55, memPercent: 70, memTotalMB: 16384, memUsedMB: 11469 } },
      appPortsByIp: {},
    }),
  );
  const csv = lib.summaryRowsCsv(rows).split('\r\n');
  assert.equal(csv[0], 'IP,服务器名称,环境,资源归属,是否纳管,服务器规格,CPU%,内存%,监听端口数,进程分类');
  assert.equal(csv[1], '10.58.0.10,10.58.0.10,-,阿里云ECS,已纳管,8C 16G,55%,70%,3,"Java Jar 1, Nginx 1"');
  assert.equal(csv[2], '10.58.0.99,10.58.0.99,-,阿里云ECS,未纳管,-,-,-,-,-');
});

test('汇总-formatUsagePercent与formatCategories工具函数', () => {
  assert.equal(lib.formatUsagePercent(12.5), '12.5%');
  assert.equal(lib.formatUsagePercent(undefined), '-');
  assert.equal(lib.formatCategories([['Tomcat', 2], ['Nginx', 1]]), 'Tomcat 2, Nginx 1');
  assert.equal(lib.formatCategories([]), '-');
});

test('长命令格式化为参数逐行并拆开超长 classpath', () => {
  assert.equal(serverProcesses.COMMAND_COLLAPSE_THRESHOLD, 256);
  const cmd = '/bin/java -server -Xms512m "-Dfoo=a b" -jar /opt/app.jar';
  assert.equal(
    serverProcesses.formatCommand(cmd),
    ['/bin/java', '-server', '-Xms512m', '"-Dfoo=a b"', '-jar', '/opt/app.jar'].join('\n'),
  );
  const classpath = '-classpath .:' + '/opt/lib/' + 'a'.repeat(120) + '.jar:/opt/lib/b.jar';
  const lines = serverProcesses.formatCommand(classpath).split('\n');
  assert.equal(lines[0], '-classpath');
  assert.equal(lines[1], '.:');
  assert.equal(lines[2], '  /opt/lib/' + 'a'.repeat(120) + '.jar:');
  assert.equal(lines[3], '  /opt/lib/b.jar');
  // 短命令保持原样，不做额外拆行。
  assert.equal(serverProcesses.formatCommand('redis-server'), 'redis-server');
});
