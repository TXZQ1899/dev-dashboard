const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');

function editor(tenants) {
  const states = [], saved = [];
  let index = 0;
  const sandbox = { exports: {}, require: id => {
    if (id === 'react') return { ...React, useState(initial) {
      const slot = index++;
      if (!(slot in states)) states[slot] = initial;
      return [states[slot], value => { states[slot] = typeof value === 'function' ? value(states[slot]) : value; }];
    } };
    if (id === 'react/jsx-runtime') return require(id);
    if (id.includes('button')) return { Button: props => React.createElement('button', props) };
    if (id.includes('input')) return { Input: props => React.createElement('input', props) };
    throw new Error(id);
  } };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/aliyun-tenant-settings.tsx', 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, sandbox);
  function render() {
    index = 0;
    return sandbox.exports.AliyunTenantSettings({ tenants, busy: false, save: async payload => saved.push(JSON.parse(JSON.stringify(payload))) });
  }
  function nodes(node) {
    if (Array.isArray(node)) return node.flatMap(nodes);
    if (!React.isValidElement(node)) return [];
    if (typeof node.type === 'function') return nodes(node.type(node.props));
    return [node, ...nodes(node.props.children)];
  }
  const all = () => nodes(render());
  return { saved,
    button: (label, occurrence = 0) => all().filter(node => node.type === 'button' && node.props.children === label)[occurrence],
    field: id => all().find(node => node.props.id === id),
  };
}

const legacy = { id: 'legacy-aliyun', name: '绑定租户', accountId: '1001', enabled: true,
  regions: ['cn-shanghai'], dmsTenantId: '', credentialVersion: 'revision-a', configured: true,
  accessKeyId: 'LTAI****mask', mode: 'accesskey', codeup: true, codeupOrganizationId: 'existing-org' };
const other = { ...legacy, id: 'tenant-b', name: '其他租户', credentialVersion: 'revision-b',
  codeup: false, codeupOrganizationId: undefined };

test('bound tenant editor retains Codeup settings while blank AccessKey fields preserve credentials', async () => {
  const view = editor([legacy]);
  view.button('管理租户').props.onClick();
  assert.equal(view.field('tenant-mode').props.value, 'accesskey');
  assert.equal(view.field('tenant-key').props.value, '');
  assert.equal(view.field('tenant-secret').props.type, 'password');
  assert.equal(view.field('tenant-codeup-organization').props.value, 'existing-org');
  view.field('tenant-codeup-organization').props.onChange({ target: { value: 'updated-org' } });
  view.button('保存租户').props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(Object.keys(view.saved[0]), ['aliyunTenants']);
  const patch = view.saved[0].aliyunTenants[0];
  assert.equal(patch.codeupOrganizationId, 'updated-org');
  assert.equal(patch.mode, 'accesskey');
  assert.equal(patch.accessKeyId, '');
  assert.equal(patch.accessKeySecret, '');
  assert.equal(patch.credentialVersion, 'revision-a');
});

test('Cookie compatibility is editable inside the bound tenant without displaying AccessKey inputs', async () => {
  const view = editor([{ ...legacy, mode: 'cookie' }]);
  view.button('管理租户').props.onClick();
  assert.equal(view.field('tenant-cookie').props.type, 'password');
  assert.equal(view.field('tenant-key'), undefined);
  assert.equal(view.button('保存并验证账号'), undefined);
  view.field('tenant-cookie').props.onChange({ target: { value: 'session=new' } });
  view.button('保存租户').props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(view.saved[0].aliyunTenants[0].cookie, 'session=new');
  assert.equal(view.saved[0].aliyunTenants[0].codeupOrganizationId, 'existing-org');
});

test('other tenants do not display or submit bound-tenant compatibility settings', async () => {
  const view = editor([other]);
  view.button('管理租户').props.onClick();
  for (const id of ['tenant-mode', 'tenant-cookie', 'tenant-codeup-organization'])
    assert.equal(view.field(id), undefined);
  assert.ok(view.field('tenant-key'));
  view.button('保存租户').props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  const patch = view.saved[0].aliyunTenants[0];
  for (const key of ['mode', 'cookie', 'codeupOrganizationId']) assert.equal(key in patch, false);
});

test('new tenants default to AccessKey and keep their own credential fields', () => {
  const view = editor([legacy]);
  view.button('新增阿里云租户').props.onClick();
  assert.ok(view.field('tenant-key'));
  assert.ok(view.field('tenant-secret'));
  assert.equal(view.field('tenant-mode'), undefined);
  assert.equal(view.field('tenant-codeup-organization'), undefined);
});
