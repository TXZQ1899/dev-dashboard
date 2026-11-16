const test = require('node:test');
const assert = require('node:assert/strict');

test('CommonJS guard skips JSON while preserving JavaScript transformation', async () => {
  const { default: vinext } = await import('vinext');
  const { excludeJsonFromCommonjs } = await import('../commonjs-json-guard.mjs');
  const plugins = excludeJsonFromCommonjs(vinext());
  const plugin = plugins.find((item) => item && item.name === 'vite-plugin-commonjs');
  const config = {
    resolve: { extensions: ['.js', '.json'] },
    optimizeDeps: { esbuildOptions: { plugins: [] } },
    createResolver: () => async () => undefined,
  };
  plugin.configResolved(config);
  assert.deepEqual(config.resolve.extensions, ['.js', '.json']);
  const content = '/*' + 'x'.repeat(1000000);
  assert.equal(await plugin.transform(content, '/tmp/snapshot.json'), undefined);
  assert.ok((await plugin.transform('module.exports = 42;', '/tmp/example.js')).code);
  const hook = config.optimizeDeps.esbuildOptions.plugins[0];
  assert.equal(hook.name, 'vite-plugin-commonjs:pre-bundle');
});

test('guard fails clearly if vinext changes its plugin', async () => {
  const { excludeJsonFromCommonjs } = await import('../commonjs-json-guard.mjs');
  assert.throws(() => excludeJsonFromCommonjs([]), /review the JSON guard/);
});
