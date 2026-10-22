const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function setup(record) {
  const elements = new Map();
  const node = () => ({
    textContent: '', children: [], dataset: {}, handlers: {},
    classList: {toggle() {}}, style: {setProperty() {}}, setAttribute() {},
    addEventListener(event, handler) { this.handlers[event] = handler; },
    querySelector() { return {}; },
    append(...items) { this.children.push(...items); },
    prepend(item) { this.children.unshift(item); },
    replaceChildren(...items) { this.children = items; },
  });
  const element = id => {
    if (!elements.has(id)) elements.set(id, node());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll: () => [], addEventListener() {},
      createElement: node, createTextNode: text => ({textContent: text})},
    window: {getSelection: () => ({isCollapsed: true})},
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

test('text editor preserves literal HTML entities', () => {
  const {context, element} = setup({text: '&amp; Jane', labels: []});
  element('edit-text').focus = () => {};
  element('edit').onclick();
  assert.equal(element('edit-text').value, '&amp; Jane');
});

test('adjacent identical labels render individually and remove independently', () => {
  const labels = [[0, 4, 'NAME'], [4, 9, 'NAME'], [9, 14, 'NAME'], [15, 20, 'NAME']];
  const {context, element} = setup({text: 'AnnaBellaClara Diana', labels});
  vm.runInContext("rendered = ''; render();", context);
  const spans = element('text').children.filter(child => child.className === 'annotation');
  assert.deepEqual(spans.map(span => span.textContent), ['Anna', 'Bella', 'Clara', 'Diana']);
  assert.equal(spans.length, labels.length);
  for (const span of spans) {
    assert.equal(span.dataset.label, 'NAME');
    assert.equal(span.children[0].dataset.label, 'NAME');
  }
  spans[1].handlers.click();
  assert.deepEqual(JSON.parse(JSON.stringify(context.change)), {
    action: 'labels', data: {labels: [labels[0], labels[2], labels[3]]},
  });
});

test('applying the same label next to another span keeps both offsets', () => {
  const {context} = setup({text: 'AnnaBella', labels: [[0, 4, 'NAME']]});
  vm.runInContext('selection = [4, 9]; applyLabel("NAME");', context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.change.data.labels)),
    [[0, 4, 'NAME'], [4, 9, 'NAME']]);
});


test('initial selection and both navigation directions skip saved input', () => {
  const {context} = setup({text: 'Saved', labels: [], saved: true});
  vm.runInContext(`state.config.settings = {skip_saved: true};
    state.texts = [record, {saved: false}, {saved: true}, {saved: false}];
    active = initialIndex();`, context);
  assert.equal(vm.runInContext('active', context), 1);
  assert.equal(vm.runInContext('navigationIndex(1)', context), 3);
  assert.equal(vm.runInContext('navigationIndex(-1)', context), -1);
  vm.runInContext('active = 3;', context);
  assert.equal(vm.runInContext('navigationIndex(-1)', context), 1);
  vm.runInContext('state.config.settings.skip_saved = false;', context);
  assert.equal(vm.runInContext('initialIndex()', context), 0);
  assert.equal(vm.runInContext('navigationIndex(-1)', context), 2);
});

test('all saved starts empty but rows remain available', () => {
  const {context, element} = setup({text: 'Saved', hash_key: 'a', labels: [], saved: true});
  vm.runInContext('active = initialIndex(); render();', context);
  assert.equal(vm.runInContext('active', context), -1);
  assert.equal(element('text').textContent, '');
  assert.equal(element('left-view').children.length, 1);
});

test('database navigation includes saved rows and disables database-only removal', () => {
  const {context, element} = setup({text: 'Saved', hash_key: 'a', labels: [], saved: true});
  vm.runInContext(`state.database = [{...record, number: 2, in_input: false},
    {...record, hash_key: 'b', number: 1, in_input: true}];
    activeView = 'database'; databaseKey = 'a'; render();`, context);
  assert.equal(vm.runInContext('navigationIndex(1)', context), 1);
  assert.equal(element('saved').disabled, true);
  assert.equal(element('counter').textContent, 'DB 2/2');
  vm.runInContext("databaseKey = 'b'; render();", context);
  assert.equal(element('saved').disabled, false);
});
