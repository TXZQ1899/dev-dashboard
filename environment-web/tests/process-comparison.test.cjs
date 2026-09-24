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

const lib = evaluate(
  path.join(__dirname, '../lib/process-comparison.ts'),
  (id) => {
    if (id === '@/lib/jumpserver') return jumpserverModule;
    if (id === '@/lib/server-processes') return {};
    if (id === '@/lib/inventory') return {};
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
