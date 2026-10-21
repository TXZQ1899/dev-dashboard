const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function evaluate(file, requireModule = () => {}) {
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

const jumpserverModule = {
  jumpserver: {
    assets: [],
    collectedAt: '2026-09-20T00:00:00+08:00',
  },
};

// inventory 运行时依赖：latestAppDeploymentTime 用 envs 遍历全部环境，
// appOverYearUndeployed / appMatchesDeploymentPeriod 用 inventoryDate 做默认基准。
const inventoryModule = {
  envs: ['TEST', 'SIMULATION', 'PRODUCT'],
  inventoryDate: '2026-09-30T12:00:00+08:00',
};

// server-specs 覆盖：新格式（含 specsCollected/loginStatus）、旧格式（仅 cpu）、多 IP 记录。
const serverSpecRows = [
  { ip: '10.58.0.1', hostname: 'a', specsCollected: true, loginStatus: 'can_login', cpu: 2, memoryMB: 3936, os: 'CentOS 7.9' },
  { ip: '10.58.0.2', hostname: 'b', specsCollected: true, loginStatus: 'cannot_login', cpu: 0, memoryMB: 0, os: '' },
  { ip: '10.58.0.3', hostname: 'c', cpu: 4, memoryMB: 8000, os: 'CentOS 7.9' },
  { ip: '10.58.0.4 10.58.0.5', hostname: 'multi', specsCollected: false, loginStatus: 'cannot_login', cpu: 0, memoryMB: 0, os: '' },
];

const ecsInstanceRows = [
  { privateIps: ['172.16.0.5'], publicIps: ['47.100.1.1'], cpu: 8, memoryGiB: 16 },
];

// process-comparison.ts 的时间格式化已委托给 lib/datetime.ts（统一北京时间实现）。
const datetimeModule = evaluate(path.join(__dirname, '../lib/datetime.ts'));

const lib = evaluate(
  path.join(__dirname, '../lib/process-comparison.ts'),
  (id) => {
    if (id === './datetime') return datetimeModule;
    if (id === '@/lib/jumpserver') return jumpserverModule;
    if (id === '@/lib/server-processes')
      return {
        // 与 lib/server-processes.ts 的 jarName 保持一致：取 -jar 参数的 basename。
        jarName: (p) => {
          const tokens = (p.command.match(/"(?:\\.|[^"\\])*"|'[^']*'|[^\s]+/g) || [])
            .map((s) => s.replace(/^(['"])(.*)\1$/, '$2'));
          const i = tokens.indexOf('-jar');
          return i >= 0 && tokens[i + 1] ? tokens[i + 1].split('/').pop() : undefined;
        },
      };
    if (id === '@/lib/inventory') return inventoryModule;
    if (id === '@/lib/server-specs') return { specs: serverSpecRows };
    if (id === '@/lib/ecs') return { instances: ecsInstanceRows };
    if (id === '@/lib/local-servers')
      return {
        nominalMemoryGiB: (memoryMB) => {
          const capacities = [2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256];
          const gib = memoryMB / 1024;
          return capacities.reduce((best, c) =>
            Math.abs(c - gib) < Math.abs(best - gib) ? c : best,
          );
        },
      };
    throw new Error(id);
  },
);

test('extractPortFromCommand captures Spring Boot and generic port flags', () => {
  assert.equal(lib.extractPortFromCommand('java -jar app.jar --server.port=8080'), '8080');
  assert.equal(lib.extractPortFromCommand('java -Dserver.port=9090 -jar app.jar'), '9090');
  assert.equal(lib.extractPortFromCommand('node server.js --port 3000'), '3000');
  assert.equal(lib.extractPortFromCommand('java -jar app.jar --port=7777'), '7777');
  assert.equal(lib.extractPortFromCommand('java -jar app.jar'), undefined);
  assert.equal(lib.extractPortFromCommand('nginx: master process /usr/sbin/nginx'), undefined);
});

test('extractPortFromCommand ignores out-of-range ports', () => {
  assert.equal(lib.extractPortFromCommand('java -jar app.jar --server.port=99999'), undefined);
  assert.equal(lib.extractPortFromCommand('java -jar app.jar --server.port=0'), undefined);
});

function makeProcess(command, name = 'java', kind = 'Java') {
  return { pid: 1, ppid: 0, user: 'app', name, kind, startedAt: '', elapsedSeconds: 0, command };
}

test('matchAppToProcess matches by jar name and app name substring', () => {
  const app = { id: '1', name: 'fosun-wechat', ports: ['8070'] };
  const processes = [
    makeProcess('java -jar /srv/fosun-wechat.jar --server.port=8080'),
    makeProcess('nginx: master process /usr/sbin/nginx', 'nginx', 'Nginx'),
  ];
  assert.equal(lib.matchAppToProcess(app, processes), processes[0]);
});

test('matchAppToProcess matches Nginx app with Nginx process', () => {
  const app = { id: '1', name: 'Nginx', ports: [] };
  const processes = [makeProcess('nginx: master process /usr/sbin/nginx', 'nginx', 'Nginx')];
  assert.equal(lib.matchAppToProcess(app, processes), processes[0]);
});

test('matchAppToProcess matches by -Dappid= convention', () => {
  const app = { id: '1', name: 'fcrs-base', ports: [] };
  const processes = [
    makeProcess('java -Xmx2G -Dappid=fcrs-base -DlogLogstashServer=10.179.1.227:4636 -jar /var/www/webapps/fcrs-base/fcrs-base-exec.jar --spring.profiles.active=test --server.port=7005'),
  ];
  assert.equal(lib.matchAppToProcess(app, processes), processes[0]);
});

test('matchAppToProcess matches by Tomcat base path convention', () => {
  const app = { id: '1', name: 'phoenix-open-web', ports: ['7114'] };
  const processes = [
    makeProcess('/opt/jdk1.8.0_101/jre/bin/java -Djava.util.logging.config.file=/opt/apache-tomcat_phoenix-open-web_37864/conf/logging.properties -Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager -server'),
  ];
  assert.equal(lib.matchAppToProcess(app, processes), processes[0]);
});

test('matchAppToProcess matches by webapps path segment', () => {
  const app = { id: '1', name: 'atlantis_nuxt', ports: [] };
  const processes = [
    makeProcess('/usr/bin/node /var/www/webapps/atlantis_nuxt/node_modules/nuxt/bin/nuxt.js', 'node', 'Node.js'),
  ];
  assert.equal(lib.matchAppToProcess(app, processes), processes[0]);
});

test('matchAppToProcess does not false-match short names like erp in properties', () => {
  const app = { id: '1', name: 'erp', ports: [] };
  const processes = [
    makeProcess('java -Djava.util.logging.config.file=/opt/tomcat/conf/logging.properties -Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager'),
  ];
  assert.equal(lib.matchAppToProcess(app, processes), undefined);
});

test('matchAppToProcess returns undefined when no process matches', () => {
  const app = { id: '1', name: 'nonexistent-app', ports: [] };
  const processes = [makeProcess('java -jar /srv/other.jar')];
  assert.equal(lib.matchAppToProcess(app, processes), undefined);
});

test('compareServerApps marks unmatched apps and supplements ports', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'a1',
      ip: '10.0.0.1',
      hostname: 'host1',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [
          makeProcess('java -jar /srv/myapp.jar --server.port=9090'),
          makeProcess('redis-server *:6379', 'redis-server', 'Redis'),
        ],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
      },
    },
  ];
  const apps = [
    { id: '1', name: 'myapp', ports: ['9090'] },
    { id: '2', name: 'missing-app', ports: [] },
    { id: '3', name: 'app-no-port', ports: [] },
  ];
  const result = lib.compareServerApps('10.0.0.1', apps);
  assert.equal(result.available, true);
  assert.equal(result.processes.length, 2);
  assert.equal(result.apps.length, 3);
  assert.equal(result.apps[0].matched, true);
  assert.equal(result.apps[1].matched, false);
  assert.equal(result.apps[2].matched, false);
  assert.equal(result.apps[0].supplementedPort, undefined);
  assert.equal(result.apps[1].supplementedPort, undefined);
});

test('compareServerApps supplements port from process for app with empty port', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'a2',
      ip: '10.0.0.2',
      hostname: 'host2',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [
          makeProcess('java -jar /srv/spring-boot-app.jar --server.port=8081'),
        ],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
      },
    },
  ];
  const apps = [{ id: '1', name: 'spring-boot-app', ports: ['未提供端口'] }];
  const result = lib.compareServerApps('10.0.0.2', apps);
  assert.equal(result.apps[0].matched, true);
  assert.equal(result.apps[0].supplementedPort, '8081');
});

test('compareServerApps identifies extra processes not matched to any app', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'a3',
      ip: '10.0.0.3',
      hostname: 'host3',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [
          makeProcess('java -jar /srv/known-app.jar'),
          makeProcess('redis-server *:6379', 'redis-server', 'Redis'),
        ],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
      },
    },
  ];
  const apps = [{ id: '1', name: 'known-app', ports: [] }];
  const result = lib.compareServerApps('10.0.0.3', apps);
  assert.equal(result.extraProcesses.length, 1);
  assert.equal(result.extraProcesses[0].name, 'redis-server');
});

test('compareServerApps returns unavailable for IP without inspection data', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'a4', ip: '10.0.0.4', hostname: 'host4' },
  ];
  const apps = [{ id: '1', name: 'app', ports: [] }];
  const result = lib.compareServerApps('10.0.0.4', apps);
  assert.equal(result.available, false);
  assert.equal(result.processes.length, 0);
  assert.equal(result.apps[0].matched, false);
});

test('compareServerApps falls back to webapps match when process does not match', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'w1',
      ip: '10.0.0.41',
      hostname: 'host41',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [makeProcess('java -jar /srv/other-app.jar')],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
        webapps: {
          status: 'complete',
          apps: [
            { name: 'frontend-portal', mtime: '2026-09-01T00:00:00+00:00' },
            { name: 'Backend_Api', mtime: '2026-09-02T00:00:00+00:00' },
          ],
        },
      },
    },
  ];
  const apps = [
    { id: '1', name: 'frontend-portal', ports: ['80'] },
    { id: '2', name: 'backend-api', ports: ['8080'] },
    { id: '3', name: 'missing-app', ports: [] },
  ];
  const result = lib.compareServerApps('10.0.0.41', apps);
  // 1: 进程不匹配，但 webapps 同名目录命中（忽略大小写/连字符/下划线）→ matched, webapp 填充
  assert.equal(result.apps[0].matched, true);
  assert.equal(result.apps[0].process, undefined);
  assert.equal(result.apps[0].webapp?.name, 'frontend-portal');
  // 2: 同样命中（Backend_Api 规范化后与 backend-api 相同）
  assert.equal(result.apps[1].matched, true);
  assert.equal(result.apps[1].process, undefined);
  assert.equal(result.apps[1].webapp?.name, 'Backend_Api');
  // 3: 进程与 webapps 均无匹配 → 仍标红
  assert.equal(result.apps[2].matched, false);
  assert.equal(result.apps[2].process, undefined);
  assert.equal(result.apps[2].webapp, undefined);
});

test('compareServerApps does not fall back to webapps when webapps is absent or not collected', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'w2',
      ip: '10.0.0.42',
      hostname: 'host42',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [makeProcess('java -jar /srv/other.jar')],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
        webapps: { status: 'absent', apps: [] },
      },
    },
    {
      id: 'w3',
      ip: '10.0.0.43',
      hostname: 'host43',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [makeProcess('java -jar /srv/other.jar')],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
      },
    },
  ];
  // absent 状态：webapps 不可用作回退
  let result = lib.compareServerApps('10.0.0.42', [{ id: '1', name: 'frontend-portal', ports: [] }]);
  assert.equal(result.apps[0].matched, false);
  assert.equal(result.apps[0].webapp, undefined);
  // 未采集 webapps：同样不可回退
  result = lib.compareServerApps('10.0.0.43', [{ id: '1', name: 'frontend-portal', ports: [] }]);
  assert.equal(result.apps[0].matched, false);
  assert.equal(result.apps[0].webapp, undefined);
});

test('compareServerApps only skips too-short webapp names, not common words', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'w4',
      ip: '10.0.0.44',
      hostname: 'host44',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [makeProcess('java -jar /srv/other.jar')],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
        webapps: {
          status: 'complete',
          apps: [
            { name: 'a', mtime: null },
            { name: 'app', mtime: null },
            { name: 'webapps', mtime: null },
            { name: 'static', mtime: null },
          ],
        },
      },
    },
  ];
  const apps = [
    { id: '1', name: 'a', ports: [] },
    { id: '2', name: 'app', ports: [] },
    { id: '3', name: 'webapps', ports: [] },
    { id: '4', name: 'static', ports: [] },
  ];
  const result = lib.compareServerApps('10.0.0.44', apps);
  // 仅长度 <2 的名称（"a"）不匹配；"app"/"webapps"/"static" 这类通用目录词也可作为合法应用名匹配
  assert.equal(result.apps[0].matched, false);
  assert.equal(result.apps[1].matched, true);
  assert.equal(result.apps[1].webapp?.name, 'app');
  assert.equal(result.apps[2].matched, true);
  assert.equal(result.apps[2].webapp?.name, 'webapps');
  assert.equal(result.apps[3].matched, true);
  assert.equal(result.apps[3].webapp?.name, 'static');
});

test('compareServerApps returns unavailable for cannot_login asset', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'a5',
      ip: '10.0.0.5',
      hostname: 'host5',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'cannot_login',
        reason: 'SSH 认证失败',
        processStatus: 'not_collected',
        processes: [],
        nginxStatus: 'not_collected',
        nginxRoutes: [],
        warnings: [],
      },
    },
  ];
  const apps = [{ id: '1', name: 'app', ports: [] }];
  const result = lib.compareServerApps('10.0.0.5', apps);
  assert.equal(result.available, false);
  assert.equal(result.loginStatus, 'cannot_login');
});

test('appHasProcessOnIp returns true when app matches a process on that IP', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'a6',
      ip: '10.0.0.6',
      hostname: 'host6',
      inspection: {
        checkedAt: '2026-09-20T00:00:00+00:00',
        loginStatus: 'can_login',
        reason: '',
        processStatus: 'complete',
        processes: [makeProcess('java -Dappid=my-app -jar /srv/my-app.jar')],
        nginxStatus: 'not_running',
        nginxRoutes: [],
        warnings: [],
      },
    },
  ];
  assert.equal(lib.appHasProcessOnIp('my-app', '10.0.0.6'), true);
  assert.equal(lib.appHasProcessOnIp('nonexistent', '10.0.0.6'), false);
});

test('appHasProcessOnIp returns false for IP without process data', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'a7', ip: '10.0.0.7', hostname: 'host7' },
  ];
  assert.equal(lib.appHasProcessOnIp('my-app', '10.0.0.7'), false);
});

test('ipServerState marks IPs missing from JumpServer as not_collected', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'a1', ip: '10.58.0.1', hostname: 'a' },
  ];
  assert.equal(lib.ipServerState('10.58.0.9'), 'not_collected');
});

test('ipServerState uses server-specs loginStatus with inspection fallback', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'a1', ip: '10.58.0.1', hostname: 'a' },
    { id: 'a2', ip: '10.58.0.2', hostname: 'b' },
    { id: 'a3', ip: '10.58.0.3', hostname: 'c' },
    { id: 'a4', ip: '10.58.0.4', hostname: 'multi-1' },
    { id: 'b1', ip: '10.0.0.8', hostname: 'h8', inspection: { loginStatus: 'can_login' } },
    { id: 'b2', ip: '10.0.0.9', hostname: 'h9', inspection: { loginStatus: 'cannot_login' } },
    { id: 'b3', ip: '10.0.0.10', hostname: 'h10' },
  ];
  assert.equal(lib.ipServerState('10.58.0.1'), 'can_login');
  assert.equal(lib.ipServerState('10.58.0.2'), 'cannot_login');
  // 旧格式：无 loginStatus 字段，回退 cpu>0 推断为可登录
  assert.equal(lib.ipServerState('10.58.0.3'), 'can_login');
  // 多 IP 记录按空白拆分后均可命中
  assert.equal(lib.ipServerState('10.58.0.4'), 'cannot_login');
  // 无 specs 记录时回退 JumpServer 巡检
  assert.equal(lib.ipServerState('10.0.0.8'), 'can_login');
  assert.equal(lib.ipServerState('10.0.0.9'), 'cannot_login');
  // 已收录但无登录证据按不能登录处理
  assert.equal(lib.ipServerState('10.0.0.10'), 'cannot_login');
});

test('ipServerState prefers explicit specs loginStatus over inspection', () => {
  jumpserverModule.jumpserver.assets = [
    {
      id: 'a1',
      ip: '10.58.0.2',
      hostname: 'b',
      inspection: { loginStatus: 'can_login' },
    },
  ];
  assert.equal(lib.ipServerState('10.58.0.2'), 'cannot_login');
});

test('ipSpecSummary renders cores and nominal memory for local and cloud servers', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'b1', ip: '10.0.0.8', hostname: 'h8', inspection: { loginStatus: 'can_login' } },
  ];
  // 3936MB → 3.84GiB → 标称 4G
  assert.equal(lib.ipSpecSummary('10.58.0.1'), '2C 4G');
  // 未采集规格（cpu=0）返回空
  assert.equal(lib.ipSpecSummary('10.58.0.2'), '');
  // 旧格式 8000MB → 7.81GiB → 标称 8G
  assert.equal(lib.ipSpecSummary('10.58.0.3'), '4C 8G');
  // specsCollected=false 不展示规格
  assert.equal(lib.ipSpecSummary('10.58.0.4'), '');
  // 云服务器取 ECS 实例规格（内网与公网 IP 均可）
  assert.equal(lib.ipSpecSummary('172.16.0.5'), '8C 16G');
  assert.equal(lib.ipSpecSummary('47.100.1.1'), '8C 16G');
  // 已收录但无任何规格数据
  assert.equal(lib.ipSpecSummary('10.0.0.8'), '');
});

function makeEnvApp(name, envRows) {
  return {
    id: '9',
    name,
    http: '',
    port: '',
    repository: '',
    envs: { TEST: envRows, SIMULATION: [], PRODUCT: [] },
  };
}

function makeAsset(id, ip, loginStatus, processes = []) {
  return {
    id,
    ip,
    hostname: id,
    inspection: {
      checkedAt: '2026-09-20T00:00:00+00:00',
      loginStatus,
      reason: '',
      processStatus: processes.length ? 'complete' : 'not_collected',
      processes,
      nginxStatus: 'not_running',
      nginxRoutes: [],
      warnings: [],
    },
  };
}

// envServerVerdicts 返回的对象产自 vm 沙箱 realm，deepStrictEqual 会因原型不同误报，
// 改为逐字段比较。
function assertVerdict(actual, expected) {
  for (const key of Object.keys(expected)) {
    assert.equal(actual[key], expected[key], `verdict.${key}`);
  }
}

test('envServerVerdicts reports matched process on loginable server', () => {
  jumpserverModule.jumpserver.assets = [
    makeAsset('c1', '10.0.0.21', 'can_login', [makeProcess('java -jar /srv/verdict-app.jar')]),
  ];
  const app = makeEnvApp('verdict-app', [
    { ip: '10.0.0.21', deploy: '', config: '', status: '成功', error: '' },
  ]);
  assertVerdict(lib.envServerVerdicts(app, 'TEST'), {
    notCollected: false,
    cannotLogin: false,
    canLogin: true,
    matched: true,
    unmatched: false,
  });
});

test('envServerVerdicts reports unmatched process on loginable server', () => {
  jumpserverModule.jumpserver.assets = [
    makeAsset('c1', '10.0.0.21', 'can_login', [makeProcess('java -jar /srv/other-app.jar')]),
  ];
  const app = makeEnvApp('verdict-app', [
    { ip: '10.0.0.21', deploy: '', config: '', status: '成功', error: '' },
  ]);
  const v = lib.envServerVerdicts(app, 'TEST');
  assert.equal(v.canLogin, true);
  assert.equal(v.unmatched, true);
  assert.equal(v.matched, false);
});

test('envServerVerdicts distinguishes cannot login and not collected servers', () => {
  jumpserverModule.jumpserver.assets = [
    makeAsset('c2', '10.0.0.22', 'cannot_login'),
  ];
  const app = makeEnvApp('verdict-app', [
    { ip: '10.0.0.22', deploy: '', config: '', status: '成功', error: '' },
    { ip: '10.0.0.99', deploy: '', config: '', status: '成功', error: '' },
  ]);
  assertVerdict(lib.envServerVerdicts(app, 'TEST'), {
    notCollected: true,
    cannotLogin: true,
    canLogin: false,
    matched: false,
    unmatched: false,
  });
});

test('envServerVerdicts aggregates each environment independently', () => {
  jumpserverModule.jumpserver.assets = [
    makeAsset('c1', '10.0.0.21', 'can_login', [makeProcess('java -jar /srv/verdict-app.jar')]),
    makeAsset('c2', '10.0.0.22', 'cannot_login'),
  ];
  const app = makeEnvApp('verdict-app', []);
  app.envs.TEST = [{ ip: '10.0.0.21', deploy: '', config: '', status: '成功', error: '' }];
  app.envs.PRODUCT = [
    { ip: '10.0.0.22', deploy: '', config: '', status: '成功', error: '' },
    { ip: '10.0.0.99', deploy: '', config: '', status: '成功', error: '' },
  ];
  assertVerdict(lib.envServerVerdicts(app, 'TEST'), {
    notCollected: false,
    cannotLogin: false,
    canLogin: true,
    matched: true,
    unmatched: false,
  });
  assertVerdict(lib.envServerVerdicts(app, 'PRODUCT'), {
    notCollected: true,
    cannotLogin: true,
    canLogin: false,
    matched: false,
    unmatched: false,
  });
});

// ---- latestAppDeploymentTime / appOverYearUndeployed / appMatchesDeploymentPeriod ----
// 这些函数用 JumpServer 进程启动时间与 webapps 目录 mtime 替代 DevOps Push In 时间。

function makeProc(command, startedAt, name = 'java', kind = 'Java') {
  return { pid: 1, ppid: 0, user: 'app', name, kind, startedAt, elapsedSeconds: 0, command };
}

function makeInspection(loginStatus, processes, webapps) {
  const insp = {
    checkedAt: '2026-09-20T00:00:00+00:00',
    loginStatus,
    reason: '',
    processStatus: processes.length ? 'complete' : 'not_collected',
    processes,
    nginxStatus: 'not_running',
    nginxRoutes: [],
    warnings: [],
  };
  if (webapps) insp.webapps = webapps;
  return insp;
}

test('latestAppDeploymentTime uses matched process startedAt', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd1', ip: '10.0.0.31', hostname: 'h31', inspection: makeInspection('can_login', [
      makeProc('java -jar /srv/deploy-app.jar --server.port=8080', '2026-09-10T08:00:00+00:00'),
    ]) },
  ];
  const app = makeEnvApp('deploy-app', [{ ip: '10.0.0.31', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '2026-09-10T08:00:00+00:00');
  // 全部环境也应命中
  assert.equal(lib.latestAppDeploymentTime(app), '2026-09-10T08:00:00+00:00');
});

test('latestAppDeploymentTime uses webapp mtime when process does not match', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd2', ip: '10.0.0.32', hostname: 'h32', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/other.jar', '2026-09-01T00:00:00+00:00')],
      { status: 'complete', apps: [{ name: 'frontend-portal', mtime: '2026-09-15T00:00:00+00:00' }] }) },
  ];
  const app = makeEnvApp('frontend-portal', [{ ip: '10.0.0.32', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '2026-09-15T00:00:00+00:00');
});

test('latestAppDeploymentTime picks the latest of process time and webapp mtime', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd3', ip: '10.0.0.33', hostname: 'h33', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/mix-app.jar', '2026-09-05T00:00:00+00:00')],
      { status: 'complete', apps: [{ name: 'mix-app', mtime: '2026-09-20T00:00:00+00:00' }] }) },
  ];
  const app = makeEnvApp('mix-app', [{ ip: '10.0.0.33', deploy: '', config: '', status: '成功', error: '' }]);
  // webapp mtime 更新 → 取 mtime
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '2026-09-20T00:00:00+00:00');
  // 反过来进程更新 → 取进程时间
  jumpserverModule.jumpserver.assets[0].inspection.processes[0].startedAt = '2026-09-25T00:00:00+00:00';
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '2026-09-25T00:00:00+00:00');
});

test('latestAppDeploymentTime returns empty when neither process nor webapp matches', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd4', ip: '10.0.0.34', hostname: 'h34', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/unrelated.jar', '2026-09-01T00:00:00+00:00')],
      { status: 'complete', apps: [{ name: 'other-dir', mtime: '2026-09-01T00:00:00+00:00' }] }) },
  ];
  const app = makeEnvApp('no-match-app', [{ ip: '10.0.0.34', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '');
  assert.equal(lib.latestAppDeploymentTime(app), '');
});

test('latestAppDeploymentTime takes the latest across multiple IPs', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd5', ip: '10.0.0.35', hostname: 'h35', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/multi.jar', '2026-09-02T00:00:00+00:00')]) },
    { id: 'd6', ip: '10.0.0.36', hostname: 'h36', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/multi.jar', '2026-09-18T00:00:00+00:00')]) },
  ];
  const app = makeEnvApp('multi', [
    { ip: '10.0.0.35', deploy: '', config: '', status: '成功', error: '' },
    { ip: '10.0.0.36', deploy: '', config: '', status: '成功', error: '' },
  ]);
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '2026-09-18T00:00:00+00:00');
});

test('latestAppDeploymentTime returns empty for app without any deployment IPs', () => {
  jumpserverModule.jumpserver.assets = [];
  const app = makeEnvApp('no-deploy', []);
  assert.equal(lib.latestAppDeploymentTime(app, 'TEST'), '');
  assert.equal(lib.latestAppDeploymentTime(app), '');
});

test('appOverYearUndeployed returns false when env has no servers', () => {
  jumpserverModule.jumpserver.assets = [];
  const app = makeEnvApp('app', []);
  assert.equal(lib.appOverYearUndeployed(app, 'TEST', '2026-09-30T12:00:00+08:00'), false);
});

test('appOverYearUndeployed returns true when servers exist but no deployment record', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd7', ip: '10.0.0.37', hostname: 'h37', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/other.jar', '2026-09-01T00:00:00+00:00')]) },
  ];
  const app = makeEnvApp('no-record', [{ ip: '10.0.0.37', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.appOverYearUndeployed(app, 'TEST', '2026-09-30T12:00:00+08:00'), true);
});

test('appOverYearUndeployed returns false for recent deployment and true for over-year', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd8', ip: '10.0.0.38', hostname: 'h38', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/recent.jar', '2026-09-01T00:00:00+00:00')]) },
  ];
  const app = makeEnvApp('recent', [{ ip: '10.0.0.38', deploy: '', config: '', status: '成功', error: '' }]);
  // 近期部署 → 非超年
  assert.equal(lib.appOverYearUndeployed(app, 'TEST', '2026-09-30T12:00:00+08:00'), false);
  // 改成超过一年 → 超年
  jumpserverModule.jumpserver.assets[0].inspection.processes[0].startedAt = '2025-08-01T00:00:00+00:00';
  assert.equal(lib.appOverYearUndeployed(app, 'TEST', '2026-09-30T12:00:00+08:00'), true);
});

test('appMatchesDeploymentPeriod passes when no env or period selected', () => {
  jumpserverModule.jumpserver.assets = [];
  const app = makeEnvApp('app', []);
  assert.equal(lib.appMatchesDeploymentPeriod(app, '', '', '2026-09-30T12:00:00+08:00'), true);
  assert.equal(lib.appMatchesDeploymentPeriod(app, 'TEST', '', '2026-09-30T12:00:00+08:00'), true);
});

test('formatBeijingTime converts UTC ISO microsecond timestamp to Beijing wall time', () => {
  assert.equal(lib.formatBeijingTime('2026-09-28T01:55:21.608206+00:00'), '2026-09-28 09:55:21');
});

test('formatBeijingTime keeps +08:00 ISO and naive space-formatted values as Beijing time', () => {
  assert.equal(lib.formatBeijingTime('2026-09-30T15:30:26+08:00'), '2026-09-30 15:30:26');
  // 无时区的 "YYYY-MM-DD HH:MM:SS" 按北京时间解析
  assert.equal(lib.formatBeijingTime('2026-09-30 15:30:26'), '2026-09-30 15:30:26');
});

test('formatBeijingTime returns empty string for empty or unparsable input', () => {
  assert.equal(lib.formatBeijingTime(''), '');
  assert.equal(lib.formatBeijingTime('not-a-date'), '');
});

function makePidProc(pid, command, name = 'java', kind = 'Java') {
  return { pid, ppid: 1, user: 'app', name, kind, startedAt: '', elapsedSeconds: 0, command };
}

test('appMatchOnIp returns process pid/name/port from command line port flag', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'n1', ip: '10.0.0.51', hostname: 'h51', inspection: makeInspection('can_login', [
      makePidProc(384, 'java -Dappid=cashier -jar /srv/cashier.jar --server.port=7003'),
    ]) },
  ];
  const app = makeEnvApp('cashier', [{ ip: '10.0.0.51', deploy: '', config: '', status: '成功', error: '' }]);
  const note = lib.appMatchOnIp(app, '10.0.0.51');
  assert.equal(note.kind, 'process');
  assert.equal(note.pid, 384);
  // 业务名取 -jar 的 jar 包名，而不是运行时名 java
  assert.equal(note.processName, 'cashier.jar');
  assert.equal(note.port, '7003');
});

test('processBusinessName prefers jar name then tomcat base and falls back to process name', () => {
  // -jar 包名（含完整路径时取 basename）
  assert.equal(
    lib.processBusinessName(makePidProc(1, 'java -Xmx2G -Dappid=fcrs-base -jar /var/www/webapps/fcrs-base/fcrs-base-exec.jar --server.port=7005')),
    'fcrs-base-exec.jar',
  );
  // 显式 -Dcatalina.base
  assert.equal(
    lib.processBusinessName(makePidProc(2, '/opt/jdk/bin/java -Dcatalina.base=/opt/apache-tomcat_phoenix-open-web_37864 -Dcatalina.home=/opt/apache-tomcat_phoenix-open-web_37864 org.apache.catalina.startup.Bootstrap start')),
    '/opt/apache-tomcat_phoenix-open-web_37864',
  );
  // 无 catalina 参数时从 logging.config.file 中的 apache-tomcat_* 路径提取
  assert.equal(
    lib.processBusinessName(makePidProc(3, '/opt/jdk1.8.0_101/jre/bin/java -Djava.util.logging.config.file=/opt/apache-tomcat_phoenix-open-web_37864/conf/logging.properties -Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager -server')),
    '/opt/apache-tomcat_phoenix-open-web_37864',
  );
  // 非 Java 进程回退进程名本身
  assert.equal(
    lib.processBusinessName(makePidProc(4, 'nginx: master process /usr/sbin/nginx -c /etc/nginx/nginx.conf', 'nginx', 'Nginx')),
    'nginx',
  );
  assert.equal(
    lib.processBusinessName(makePidProc(5, '/usr/bin/node /var/www/webapps/atlantis_nuxt/node_modules/nuxt/bin/nuxt.js', 'node', 'Node')),
    'node',
  );
});

test('appMatchOnIp uses tomcat base directory as business name', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'n7', ip: '10.0.0.57', hostname: 'h57', inspection: makeInspection('can_login', [
      makePidProc(401, '/opt/jdk1.8.0_101/jre/bin/java -Djava.util.logging.config.file=/opt/apache-tomcat_phoenix-open-web_37864/conf/logging.properties -Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager -Dcatalina.base=/opt/apache-tomcat_phoenix-open-web_37864'),
    ]) },
  ];
  const app = makeEnvApp('phoenix-open-web', [{ ip: '10.0.0.57', deploy: '', config: '', status: '成功', error: '' }]);
  const note = lib.appMatchOnIp(app, '10.0.0.57');
  assert.equal(note.kind, 'process');
  assert.equal(note.processName, '/opt/apache-tomcat_phoenix-open-web_37864');
});

test('tomcatInstanceName extracts business name from catalina home/base', () => {
  // 用户案例：-Dcatalina.home=/opt/apache-tomcat_dolphin-webhook → dolphin-webhook
  assert.equal(
    lib.tomcatInstanceName(makePidProc(23251, '/opt/jdk1.8.0_101/jre/bin/java -Djava.util.logging.config.file=/opt/apache-tomcat_dolphin-webhook/conf/logging.properties -Dcatalina.base=/opt/apache-tomcat_dolphin-webhook -Dcatalina.home=/opt/apache-tomcat_dolphin-webhook -classpath /opt/apache-tomcat_dolphin-webhook/bin/bootstrap.jar org.apache.catalina.startup.Bootstrap start').command),
    'dolphin-webhook',
  );
  // catalina.base 优先于 catalina.home
  assert.equal(
    lib.tomcatInstanceName('java -Dcatalina.base=/opt/apache-tomcat_a-base -Dcatalina.home=/opt/apache-tomcat_b-home org.apache.catalina.startup.Bootstrap'),
    'a-base',
  );
  // 无 catalina 参数时从命令行 apache-tomcat_* 路径回退提取（保留 PID 后缀，关联时仍按命令匹配应用）
  assert.equal(
    lib.tomcatInstanceName(makePidProc(3, '/opt/jdk/bin/java -Djava.util.logging.config.file=/opt/apache-tomcat_phoenix-open-web_37864/conf/logging.properties -server').command),
    'phoenix-open-web_37864',
  );
  // 连字符分隔同样支持
  assert.equal(
    lib.tomcatInstanceName('java -Dcatalina.home=/opt/tomcat-simple-app org.apache.catalina.startup.Bootstrap'),
    'simple-app',
  );
  // 通用 tomcat 目录名（无业务名段）与非 Tomcat 命令返回 undefined
  assert.equal(lib.tomcatInstanceName('java -Dcatalina.home=/opt/tomcat org.apache.catalina.startup.Bootstrap'), undefined);
  assert.equal(lib.tomcatInstanceName('java -Dcatalina.home=/opt/apache-tomcat org.apache.catalina.startup.Bootstrap'), undefined);
  assert.equal(lib.tomcatInstanceName('java -jar /srv/app.jar'), undefined);
});

test('tomcatProcessApp links the deployed app matching the tomcat instance', () => {
  const dolphinCmd = makePidProc(23251, '/opt/jdk1.8.0_101/jre/bin/java -Dcatalina.base=/opt/apache-tomcat_dolphin-webhook -Dcatalina.home=/opt/apache-tomcat_dolphin-webhook -classpath /opt/apache-tomcat_dolphin-webhook/bin/bootstrap.jar org.apache.catalina.startup.Bootstrap start');
  const dolphinApp = { ...makeEnvApp('dolphin-webhook', [{ ip: '10.58.1.34' }]), id: '1441' };
  // 同机部署且进程匹配 → 命中应用 1441
  assert.equal(lib.tomcatProcessApp('10.58.1.34', dolphinCmd, [dolphinApp])?.id, '1441');
  // 应用部署在其他机器 → 不关联
  assert.equal(lib.tomcatProcessApp('10.58.1.35', dolphinCmd, [dolphinApp]), undefined);
  // IP trim 后精确比较
  assert.equal(lib.tomcatProcessApp(' 10.58.1.34 ', dolphinCmd, [dolphinApp])?.id, '1441');
});

test('tomcatProcessApp prefers exact instance-name match over substring candidates', () => {
  // dolphin-webhook 与 dolphin-webhook-batch 都在本机有部署记录，且通用子串规则
  // 都会命中 batch 实例的命令行；实例名精确匹配应选中 batch 应用自身。
  const batchCmd = makePidProc(2001, 'java -Dcatalina.home=/opt/apache-tomcat_dolphin-webhook-batch org.apache.catalina.startup.Bootstrap start');
  const core = { ...makeEnvApp('dolphin-webhook', [{ ip: '10.58.1.34' }]), id: '1441' };
  const batch = { ...makeEnvApp('dolphin-webhook-batch', [{ ip: '10.58.1.34' }]), id: '1500' };
  assert.equal(lib.tomcatProcessApp('10.58.1.34', batchCmd, [core, batch])?.id, '1500');
  // 实例目录带 _PID 后缀时规范化不相等，回退到首个按命令匹配的候选应用
  const pidCmd = makePidProc(37864, 'java -Dcatalina.home=/opt/apache-tomcat_phoenix-open-web_37864 org.apache.catalina.startup.Bootstrap start');
  const phoenix = { ...makeEnvApp('phoenix-open-web', [{ ip: '10.58.1.34' }]), id: '1600' };
  assert.equal(lib.tomcatProcessApp('10.58.1.34', pidCmd, [phoenix])?.id, '1600');
  // 本机无任何匹配应用 → undefined
  assert.equal(lib.tomcatProcessApp('10.58.1.34', batchCmd, []), undefined);
});

test('jarBusinessName strips .jar/.war suffix and keeps original case', () => {
  assert.equal(lib.jarBusinessName('java -jar /srv/App-Api.jar --server.port=8080'), 'App-Api');
  assert.equal(lib.jarBusinessName('java -Xmx2G -jar /var/www/webapps/fcrs-base/fcrs-base-exec.war'), 'fcrs-base-exec');
  // 仅保留 basename，去掉路径
  assert.equal(lib.jarBusinessName('java -jar /opt/app/bin/target/cashier.jar'), 'cashier');
  // 非 -jar 启动 → undefined
  assert.equal(lib.jarBusinessName('java -Dcatalina.home=/opt/apache-tomcat_x org.apache.catalina.startup.Bootstrap'), undefined);
  assert.equal(lib.jarBusinessName('nginx: master process /usr/sbin/nginx'), undefined);
});

test('jarProcessApp links the deployed app matching the jar name', () => {
  const jarCmd = makePidProc(23252, 'java -Xmx2G -jar /srv/cashier.jar --server.port=7003');
  const cashier = { ...makeEnvApp('cashier', [{ ip: '10.58.1.34' }]), id: '1441' };
  // 同机部署且 jar 名匹配 → 命中应用 1441
  assert.equal(lib.jarProcessApp('10.58.1.34', jarCmd, [cashier])?.id, '1441');
  // 应用部署在其他机器 → 不关联
  assert.equal(lib.jarProcessApp('10.58.1.35', jarCmd, [cashier]), undefined);
  // 进程是其他 jar、本机部署记录不匹配 → undefined
  assert.equal(lib.jarProcessApp('10.58.1.34', makePidProc(1, 'java -jar /srv/other.jar'), [cashier]), undefined);
});

test('jarProcessApp prefers exact jar-name match over substring candidates', () => {
  // cashier 与 cashier-batch 都在本机，且通用子串规则都会命中 batch 的 jar 名；
  // jar 名精确匹配应选中 batch 应用自身。
  const batchCmd = makePidProc(2002, 'java -jar /srv/cashier-batch.jar');
  const core = { ...makeEnvApp('cashier', [{ ip: '10.58.1.34' }]), id: '1441' };
  const batch = { ...makeEnvApp('cashier-batch', [{ ip: '10.58.1.34' }]), id: '1500' };
  assert.equal(lib.jarProcessApp('10.58.1.34', batchCmd, [core, batch])?.id, '1500');
  // 本机无任何匹配应用 → undefined
  assert.equal(lib.jarProcessApp('10.58.1.34', batchCmd, []), undefined);
});

test('appMatchOnIp prefers appPorts listening port correlated by pid over command flag', () => {
  const inspection = makeInspection('can_login', [
    makePidProc(385, 'java -Dappid=port-app -jar /srv/port-app.jar --server.port=7003'),
  ]);
  inspection.appPorts = [
    { app: 'port-app', kind: 'Java', port: 7004, addresses: ['0.0.0.0'], pids: [385] },
  ];
  jumpserverModule.jumpserver.assets = [
    { id: 'n2', ip: '10.0.0.52', hostname: 'h52', inspection },
  ];
  const app = makeEnvApp('port-app', [{ ip: '10.0.0.52', deploy: '', config: '', status: '成功', error: '' }]);
  const note = lib.appMatchOnIp(app, '10.0.0.52');
  assert.equal(note.kind, 'process');
  assert.equal(note.port, '7004');
});

test('appMatchOnIp joins multiple listening ports and falls back to declared port', () => {
  const inspection = makeInspection('can_login', [
    makePidProc(386, 'java -Dappid=multi-port -jar /srv/multi-port.jar'),
  ]);
  inspection.appPorts = [
    { app: 'multi-port', kind: 'Java', port: 7001, addresses: ['0.0.0.0'], pids: [386] },
    { app: 'multi-port', kind: 'Java', port: 7002, addresses: ['0.0.0.0'], pids: [386] },
    { app: 'other', kind: 'Java', port: 9999, addresses: ['0.0.0.0'], pids: [999] },
  ];
  jumpserverModule.jumpserver.assets = [
    { id: 'n3', ip: '10.0.0.53', hostname: 'h53', inspection },
  ];
  const app = makeEnvApp('multi-port', [{ ip: '10.0.0.53', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.appMatchOnIp(app, '10.0.0.53').port, '7001、7002');

  // 无 appPorts 关联、命令行也无端口参数时回退清单声明端口
  jumpserverModule.jumpserver.assets = [
    { id: 'n4', ip: '10.0.0.54', hostname: 'h54', inspection: makeInspection('can_login', [
      makePidProc(387, 'java -Dappid=declared-port -jar /srv/declared-port.jar'),
    ]) },
  ];
  const app2 = makeEnvApp('declared-port', [{ ip: '10.0.0.54', deploy: '', config: '', status: '成功', error: '' }]);
  const note2 = lib.appMatchOnIp(app2, '10.0.0.54', '8088');
  assert.equal(note2.kind, 'process');
  assert.equal(note2.port, '8088');
  // 无任何端口来源时为空串（页面展示「—」）
  assert.equal(lib.appMatchOnIp(app2, '10.0.0.54').port, '');
});

test('appMatchOnIp falls back to webapps directory when process unmatched', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'n5', ip: '10.0.0.55', hostname: 'h55', inspection: makeInspection('can_login',
      [makePidProc(388, 'java -jar /srv/other.jar')],
      { status: 'complete', apps: [{ name: 'frontend-portal', mtime: '2026-09-15T00:00:00+00:00' }] }) },
  ];
  const app = makeEnvApp('frontend-portal', [{ ip: '10.0.0.55', deploy: '', config: '', status: '成功', error: '' }]);
  const note = lib.appMatchOnIp(app, '10.0.0.55');
  assert.equal(note.kind, 'webapp');
  assert.equal(note.webappName, 'frontend-portal');
});

test('appMatchOnIp returns undefined when neither process nor webapp matches', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'n6', ip: '10.0.0.56', hostname: 'h56', inspection: makeInspection('can_login',
      [makePidProc(389, 'java -jar /srv/other.jar')],
      { status: 'complete', apps: [{ name: 'other-dir', mtime: null }] }) },
  ];
  const app = makeEnvApp('no-match-app', [{ ip: '10.0.0.56', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.appMatchOnIp(app, '10.0.0.56'), undefined);
  // 未收录/无巡检的 IP 同样返回 undefined
  assert.equal(lib.appMatchOnIp(app, '10.0.0.99'), undefined);
});

test('appMatchesDeploymentPeriod filters by latestAppDeploymentTime', () => {
  jumpserverModule.jumpserver.assets = [
    { id: 'd9', ip: '10.0.0.39', hostname: 'h39', inspection: makeInspection('can_login',
      [makeProc('java -jar /srv/period-app.jar', '2026-09-25T00:00:00+00:00')]) },
  ];
  const app = makeEnvApp('period-app', [{ ip: '10.0.0.39', deploy: '', config: '', status: '成功', error: '' }]);
  const now = '2026-09-30T12:00:00+08:00';
  // 2026-09-25 在一个星期内
  assert.equal(lib.appMatchesDeploymentPeriod(app, 'TEST', 'week', now), true);
  assert.equal(lib.appMatchesDeploymentPeriod(app, 'TEST', 'month', now), true);
  // 没有部署记录的不在区间内
  const empty = makeEnvApp('nope', [{ ip: '10.0.0.39', deploy: '', config: '', status: '成功', error: '' }]);
  assert.equal(lib.appMatchesDeploymentPeriod(empty, 'TEST', 'week', now), false);
  // 超过一年
  jumpserverModule.jumpserver.assets[0].inspection.processes[0].startedAt = '2025-08-01T00:00:00+00:00';
  assert.equal(lib.appMatchesDeploymentPeriod(app, 'TEST', 'older', now), true);
  assert.equal(lib.appMatchesDeploymentPeriod(app, 'TEST', 'year', now), false);
});

// ---- parseIpList / queryAppsByIpSet：按 IP 集批量查询 ----

function makeFullApp(id, name, appType, envIps) {
  const row = (ip) => ({ ip, deploy: '', config: '', status: '成功', error: '' });
  return {
    id,
    name,
    appType,
    http: '',
    port: '',
    repository: '',
    envs: {
      TEST: (envIps.TEST || []).map(row),
      SIMULATION: (envIps.SIMULATION || []).map(row),
      PRODUCT: (envIps.PRODUCT || []).map(row),
    },
  };
}

const ipInventory = [
  makeFullApp('a1', 'alpha-service', 'app', { TEST: ['10.0.0.1'], PRODUCT: ['10.0.0.1', '10.0.0.2'] }),
  makeFullApp('a2', 'beta', 'node', { TEST: ['10.0.0.2'] }),
  makeFullApp('a3', 'ten-app', 'node-backend', { PRODUCT: ['10.0.0.10'] }),
];

test('parseIpList splits by newline, comma, space, semicolon and trims', () => {
  assert.equal(
    lib.parseIpList('10.0.0.1\n10.0.0.2, 10.0.0.3;10.0.0.4').join('|'),
    '10.0.0.1|10.0.0.2|10.0.0.3|10.0.0.4',
  );
});

test('parseIpList dedups and keeps first-seen order', () => {
  assert.equal(
    lib.parseIpList(' 10.0.0.2 \n10.0.0.1\n10.0.0.2 ').join('|'),
    '10.0.0.2|10.0.0.1',
  );
});

test('parseIpList returns empty for blank input', () => {
  assert.equal(lib.parseIpList('  \n , ').length, 0);
});

test('queryAppsByIpSet returns one row per IP in input order', () => {
  const rows = lib.queryAppsByIpSet(['10.0.0.1', '10.0.0.9'], ipInventory);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].ip, '10.0.0.1');
  assert.equal(rows[1].ip, '10.0.0.9');
});

test('queryAppsByIpSet matches apps and their environments', () => {
  const row = lib.queryAppsByIpSet(['10.0.0.1'], ipInventory)[0];
  assert.equal(row.matched, true);
  assert.equal(row.hits.length, 1);
  assert.equal(row.hits[0].appId, 'a1');
  assert.equal(row.hits[0].appName, 'alpha-service');
  assert.equal(row.hits[0].appType, 'app');
  assert.equal(row.hits[0].envs.join(','), 'TEST,PRODUCT');
});

test('queryAppsByIpSet matches an IP shared by multiple apps', () => {
  const hits = lib.queryAppsByIpSet(['10.0.0.2'], ipInventory)[0].hits;
  const byId = Object.fromEntries(
    hits.map((h) => [h.appId, h.envs.join(',')]),
  );
  assert.equal(hits.map((h) => h.appId).join(','), 'a1,a2');
  assert.equal(byId.a1, 'PRODUCT');
  assert.equal(byId.a2, 'TEST');
});

test('queryAppsByIpSet uses exact match, not substring', () => {
  // 10.0.0.1 不得误命中部署在 10.0.0.10 上的应用
  assert.equal(
    lib.queryAppsByIpSet(['10.0.0.1'], ipInventory)[0]
      .hits.map((h) => h.appId)
      .join(','),
    'a1',
  );
  // 10.0.0.10 仅命中 ten-app
  assert.equal(
    lib.queryAppsByIpSet(['10.0.0.10'], ipInventory)[0]
      .hits.map((h) => h.appId)
      .join(','),
    'a3',
  );
});

test('queryAppsByIpSet marks unmatched IP with empty hits', () => {
  const row = lib.queryAppsByIpSet(['10.0.0.99'], ipInventory)[0];
  assert.equal(row.matched, false);
  assert.equal(row.hits.length, 0);
});

test('queryAppsByIpSet builds one row for deduped input', () => {
  const rows = lib.queryAppsByIpSet(
    lib.parseIpList('10.0.0.1\n10.0.0.1'),
    ipInventory,
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].matched, true);
});
