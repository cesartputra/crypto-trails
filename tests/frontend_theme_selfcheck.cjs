// Optional browser-free theme regression: node tests/frontend_theme_selfcheck.cjs
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../static/app.js'), 'utf8');
const themeSource = source.slice(source.indexOf('function applyTheme('));
function mount(saved, prefersLight, denyStorage = false) {
  let listener;
  const data = new Map(saved ? [['crypto-trails-theme', saved]] : []);
  const button = { attributes: {}, setAttribute(key, value) { this.attributes[key] = value; }, addEventListener(event, fn) { listener = fn; } };
  const context = { document: { documentElement: { dataset: {} } }, $: () => button,
    window: { matchMedia: () => ({ matches: prefersLight }) },
    localStorage: { getItem(key) { if (denyStorage) throw Error('blocked'); return data.get(key); },
      setItem(key, value) { if (denyStorage) throw Error('blocked'); data.set(key, value); } } };
  vm.runInNewContext(themeSource, context);
  return { context, button, data, click: () => listener() };
}
const initial = mount(null, true);
assert.equal(initial.context.document.documentElement.dataset.theme, 'light');
initial.click();
assert.equal(initial.context.document.documentElement.dataset.theme, 'dark');
assert.equal(initial.data.get('crypto-trails-theme'), 'dark');
assert.equal(initial.button.attributes['aria-label'], 'Aktifkan Light mode');
const reload = mount(initial.data.get('crypto-trails-theme'), true);
assert.equal(reload.context.document.documentElement.dataset.theme, 'dark');
reload.click();
assert.equal(reload.button.attributes['aria-pressed'], 'true');
const privateBrowser = mount(null, false, true);
privateBrowser.click();
assert.equal(privateBrowser.context.document.documentElement.dataset.theme, 'light');
console.log('PASS: system theme, toggle, persistence, aria state, blocked storage fallback.');
