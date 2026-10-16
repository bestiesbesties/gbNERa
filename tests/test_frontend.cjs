const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function setup(record) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', classList: {toggle() {}}, setAttribute() {},
      addEventListener() {}, querySelector() { return {}; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll: () => [], addEventListener() {}},
  });
  const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
  vm.runInContext(source.replace(/poll\(\);\s*$/, ''), context);
  context.record = record;
  vm.runInContext(`state = {texts: [record], config: {labels: []}, status: {}};
    rendered = JSON.stringify([active, record.text, record.labels, state.config.labels]);
    mutate = (action, data) => { globalThis.change = {action, data}; };`, context);
  return {context, element};
}

test('selection trims outside whitespace and preserves emoji and internal spaces', () => {
  const {context} = setup({text: '😀  Jane Doe \t', labels: []});
  vm.runInContext('selection = [1, 13]; applyLabel("NAME");', context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.change)), {
    action: 'labels', data: {labels: [[3, 11, 'NAME']]},
  });
});

test('whitespace-only selection does not create an annotation', () => {
  const {context, element} = setup({text: 'A   B', labels: []});
  vm.runInContext('selection = [1, 4]; applyLabel("NAME");', context);
  assert.equal(context.change, undefined);
  assert.match(element('message').textContent, /whitespace/);
});

test('saved annotation list tracks edits and save status', () => {
  const {context, element} = setup({text: 'Jane', labels: [[0, 4, 'NAME']], saved: true, dirty: false});
  vm.runInContext('render();', context);
  assert.equal(element('annotation-list').textContent, '[[0, 4, "NAME"]]');
  assert.equal(element('text-status').textContent, 'Saved in database');
  vm.runInContext(`record.labels = []; record.dirty = true;
    rendered = JSON.stringify([active, record.text, record.labels, state.config.labels]); render();`, context);
  assert.equal(element('annotation-list').textContent, '[]');
  assert.equal(element('text-status').textContent, 'Saved in database · unsaved annotation changes');
  vm.runInContext('record.dirty = false; render();', context);
  assert.equal(element('text-status').textContent, 'Saved in database');
});
