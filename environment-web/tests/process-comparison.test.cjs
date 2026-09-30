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

const lib = evaluate(
  path.join(__dirname, '../lib/process-comparison.ts'),
  (id) => {
    if (id === '@/lib/jumpserver') return jumpserverModule;
    if (id === '@/lib/server-processes') return {};
    if (id === '@/lib/inventory') return {};
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
