let state = null;
let active = 0;
let selection = null;
let editing = false;
let busy = false;
let rendered = '';
let activated = '';
let refreshNumber = 0;
let pollTimer;
const $ = id => document.getElementById(id);
const current = () => state?.texts[active];

function message(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}
async function api(path, data) {
  const response = await fetch(path, data ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
async function refresh() {
  const number = ++refreshNumber;
  const result = await api('/api/state');
  if (number !== refreshNumber) return;
  state = result;
  active = Math.min(active, Math.max(0, state.texts.length - 1));
  render();
  await activateCurrent();
  schedulePoll();
}
async function activateCurrent() {
  const record = current();
  if (!record) return;
  const token = JSON.stringify([state.revision, active, record.hash_key]);
  if (token === activated) return;
  activated = token;
  try {
    await api(`/api/text/${active}/activate`, {hash_key: record.hash_key, revision: state.revision});
    await refresh();
  } catch (error) {
    if (activated === token) activated = "";
    message(error.message, true);
  }
}
function render() {
  for (const form of document.querySelectorAll('.live-item')) {
    const key = form.dataset.key;
    const input = form.querySelector('input');
    if (document.activeElement !== input) input.value = state.config.settings[key];
    const [kind, detail] = state.status[key] || ['off', 'Not configured'];
    const indicator = form.querySelector('.status');
    indicator.src = '/icons/' + ({ok: 'connected', error: 'warning', busy: 'still_spinner', off: 'not-connected'}[kind]) + '.png';
    indicator.classList.toggle('busy', kind === 'busy');
    indicator.alt = detail;
    indicator.title = detail;
  }
  const record = current();
  $('counter').textContent = `${record ? active + 1 : 0}/${state.texts.length}`;
  $('hash').textContent = record ? 'hashkey: ' + record.hash_key : 'No text loaded';
  $('hash').title = record?.hash_key || '';
  $('previous').disabled = !record || active === 0 || editing || busy;
  $('next').disabled = !record || active === state.texts.length - 1 || editing || busy;
  $('edit').disabled = !record || busy || editing;
  $('saved').disabled = !record || busy || editing;
  $('saved').setAttribute('aria-checked', String(Boolean(record?.saved)));
  $('saved').title = record?.saved ? 'Remove text from database' : 'Save text to database';
  $('saved').setAttribute('aria-label', $('saved').title);
  $('saved').querySelector('img').src = `/icons/checkbox_${record?.saved ? 'checked' : 'unchecked'}.png`;
  $('save-changes').hidden = !(record?.saved && record.dirty);
  $('save-changes').disabled = busy || editing;
  $('text-status').textContent = !record ? '' : record.dirty ? 'Unsaved annotation changes' : record.saved ? 'Saved in database' : record.prediction === 'pending' ? 'Predicting in the background…' : record.prediction === 'error' ? 'Prediction failed: ' + (record.prediction_error || 'Unknown error') : record.prediction === 'ready' ? 'Predictions ready · not saved' : 'Not saved';
  const signature = JSON.stringify([active, record?.text, record?.labels, state.config.labels]);
  if (signature !== rendered && !editing) {
    rendered = signature;
    selection = null;
    const container = $('text');
    container.replaceChildren();
    if (!record) container.textContent = 'Load a TXT file to start labeling.';
    else {
      // Array.from uses Unicode code points, matching Python string offsets.
      const chars = Array.from(record.text);
      let offset = 0;
      for (const [start, end, name] of record.labels) {
        container.append(document.createTextNode(chars.slice(offset, start).join('')));
        const span = document.createElement('span');
        span.className = 'annotation';
        span.textContent = chars.slice(start, end).join('');
        span.dataset.label = name;
        // A zero-width inline anchor stays on the first line of a wrapped span.
        // Keep the badge in a pseudo-element so it cannot affect text offsets.
        const anchor = document.createElement('span');
        anchor.className = 'annotation-label-anchor';
        anchor.dataset.label = name;
        anchor.setAttribute('aria-hidden', 'true');
        span.prepend(anchor);
        span.style.setProperty('--color', state.config.labels.find(label => label.short_name === name)?.color || '#777777');
        span.title = `${name} · click to remove`;
        span.addEventListener('click', () => {
          if (!window.getSelection().isCollapsed || busy) return;
          mutate('labels', {labels: record.labels.filter(item => item[0] !== start)});
        });
        container.append(span);
        offset = end;
      }
      container.append(document.createTextNode(chars.slice(offset).join('')));
    }
    $('labels').replaceChildren();
    for (const label of state.config.labels) {
      const button = document.createElement('button');
      button.className = 'label-button';
      button.style.setProperty('--color', label.color);
      const name = document.createElement('span');
      name.textContent = label.short_name;
      const id = document.createElement('b');
      id.textContent = label.id;
      button.append(name, id);
      button.addEventListener('pointerdown', event => event.preventDefault());
      button.addEventListener('click', () => applyLabel(label.short_name));
      $('labels').append(button);
    }
  }
}
// Pseudo-element label badges are not text nodes and do not affect offsets.
document.addEventListener('selectionchange', () => {
  const chosen = window.getSelection();
  if (!chosen.rangeCount || chosen.isCollapsed) return;
  const range = chosen.getRangeAt(0);
  if (!$('text').contains(range.startContainer) || !$('text').contains(range.endContainer)) return;
  const prefix = document.createRange();
  prefix.selectNodeContents($('text'));
  prefix.setEnd(range.startContainer, range.startOffset);
  const start = Array.from(prefix.toString()).length;
  selection = [start, start + Array.from(range.toString()).length];
});
function applyLabel(name) {
  const record = current();
  if (busy || editing || !record) return;
  if (!selection) return message('Select part of the text first.');
  const [start, end] = selection;
  if (record.labels.some(([a, b]) => start < b && end > a)) return message('Annotations cannot overlap. Click the existing span to remove it first.', true);
  mutate('labels', {labels: [...record.labels, [start, end, name]]});
}
async function mutate(action, extra = {}) {
  if (busy || !current()) return;
  busy = true;
  render();
  try {
    await api(`/api/text/${active}`, {action, hash_key: current().hash_key, ...extra});
    if (action === 'edit') closeEditor();
    await refresh();
    message(action === 'save' ? 'Text and annotations saved.' : action === 'remove' ? 'Text removed from database.' : 'Changes are in memory. Save explicitly to keep them in the database.');
  } catch (error) { message(error.message, true); }
  finally { busy = false; render(); }
}
for (const form of document.querySelectorAll('.live-item')) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const indicator = form.querySelector('.status');
    indicator.src = '/icons/still_spinner.png';
    indicator.classList.add('busy');
    try {
      await api('/api/settings', {key: form.dataset.key, value: form.querySelector('input').value});
      if (form.dataset.key !== 'inference_url') { active = 0; closeEditor(); }
      await refresh();
      message('Setting applied. File paths are relative to the app folder unless absolute.');
    } catch (error) {
      message(error.message, true);
      indicator.src = '/icons/warning.png';
      indicator.title = error.message;
    } finally { indicator.classList.remove('busy'); busy = false; render(); }
  });
}
$('previous').onclick = () => { active--; selection = null; render(); activateCurrent(); };
$('next').onclick = () => { active++; selection = null; render(); activateCurrent(); };
$('saved').onclick = () => mutate(current().saved ? 'remove' : 'save');
$('save-changes').onclick = () => mutate('save');
$('edit').onclick = () => {
  editing = true;
  $('edit-text').value = current().text;
  $('text').hidden = true;
  $('edit-area').hidden = false;
  $('edit-text').focus();
  render();
};
function closeEditor() {
  editing = false;
  $('text').hidden = false;
  $('edit-area').hidden = true;
  rendered = '';
}
$('edit-text').addEventListener('keydown', event => {
  if (event.isComposing || busy) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeEditor();
    render();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    mutate('edit', {text: $('edit-text').value});
  }
});
document.addEventListener('keydown', event => {
  if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  if (editing || event.target.isContentEditable || event.target.closest('input, textarea, select')) return;
  const control = {ArrowLeft: 'previous', ArrowRight: 'next', Enter: 'saved'}[event.key];
  if (!control) return;
  // Override native button activation so Enter always toggles Saved exactly once.
  event.preventDefault();
  if (busy || (event.key === 'Enter' && event.repeat)) return;
  $(control).click();
});
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!state || state.status.inference_url?.[0] === 'busy') {
    pollTimer = setTimeout(poll, 900);
  }
}
async function poll() {
  try { if (!busy) await refresh(); }
  catch (error) { message('Unable to reach the app: ' + error.message, true); }
  finally { schedulePoll(); }
}
poll();
