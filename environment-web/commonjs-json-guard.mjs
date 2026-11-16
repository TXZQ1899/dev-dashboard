// vite-plugin-commonjs scans source text before running its filter. JSON needs
// no CommonJS transform, and scanning large snapshots can overflow its regex.
export function excludeJsonFromCommonjs(plugins) {
  const plugin = plugins.find((item) => item && item.name === 'vite-plugin-commonjs');
  if (!plugin || typeof plugin.configResolved !== 'function') {
    throw new Error('vinext CommonJS plugin configuration changed; review the JSON guard');
  }
  const configure = plugin.configResolved;
  plugin.configResolved = function (config) {
    return configure.call(this, {
      ...config,
      resolve: {
        ...config.resolve,
        extensions: config.resolve.extensions.filter((extension) => extension !== '.json'),
      },
    });
  };
  return plugins;
}
