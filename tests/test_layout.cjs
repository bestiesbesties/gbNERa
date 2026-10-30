const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {chromium} = require('playwright');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8')
  .replace(/{% for[\s\S]*?{% endfor %}/g, '')
  .replace(/<script[\s\S]*?<\/script>/g, '')
  .replace(/<link[^>]*>/g, '');
const script = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8')
  .replace(/poll\(\);\s*$/, '');

test('annotations preserve character positions, wrapping, text and selection offsets', async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined});
  try {
    const page = await browser.newPage();
    await page.setContent(html);
    await page.addStyleTag({path: path.join(root, 'static/style.css')});
    await page.addScriptTag({content: script});
    for (const [text, labels, width] of [
      ['jeugdzorg', [[3, 8, 'TEL']], 500],
      ['AnnaBellaClara', [[0, 4, 'TEL'], [4, 9, 'TEL'], [9, 13, 'TEL']], 500],
      ['jeugdzorg jeugdzorg jeugdzorg', [[3, 24, 'TEL']], 125],
      ['😀  jeugd\tzorg\nnext &amp;', [[3, 13, 'TEL']], 125],
    ]) {
      const result = await page.evaluate(({text, labels, width}) => {
        state = {texts: [{text, labels: []}], config: {labels: []}, status: {}};
        active = 0;
        const container = document.getElementById('text');
        container.style.width = `${width}px`;
        // Measure every character, so both extra gaps and changed wraps fail.
        const measure = () => {
          const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
          const positions = [];
          while (walker.nextNode()) {
            const node = walker.currentNode;
            let offset = 0;
            for (const char of node.data) {
              const range = document.createRange();
              range.setStart(node, offset);
              offset += char.length;
              range.setEnd(node, offset);
              const rect = range.getBoundingClientRect();
              positions.push([rect.x, rect.y, rect.width, rect.height]);
            }
          }
          return positions;
        };
        render();
        const before = measure();
        state.texts[0].labels = labels;
        render();
        const after = measure();
        const displayed = container.textContent;
        const range = document.createRange();
        range.selectNodeContents(container);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
        const selectedOffsets = [...selection];
        window.getSelection().removeAllRanges();
        mutate = (action, data) => { state.texts[0].labels = data.labels; render(); };
        while (container.querySelector('.annotation')) container.querySelector('.annotation').click();
        return {before, after, displayed, selectedOffsets, removed: measure()};
      }, {text, labels, width});
      assert.equal(result.displayed, text);
      assert.deepEqual(result.selectedOffsets, [0, Array.from(text).length]);
      for (const phase of ['after', 'removed']) {
        assert.equal(result[phase].length, result.before.length);
        result.before.forEach((rect, index) => rect.forEach((value, axis) => {
          // Splitting inline text runs can accumulate subpixel rounding.
          assert.ok(Math.abs(value - result[phase][index][axis]) < 0.1,
            `${JSON.stringify(text)}: ${phase}, character ${index}, axis ${axis} moved from ${value} to ${result[phase][index][axis]}`);
        }));
      }
    }
  } finally {
    await browser.close();
  }
});
