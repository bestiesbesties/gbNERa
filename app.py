"""A small, single-user NER editor. Run with: python app.py."""
import csv
import hashlib
import html
import re
import json
import os
from pathlib import Path
import threading
import urllib.request
from urllib.parse import urlparse

import pandas as pd
from flask import Flask, jsonify, render_template, request, send_from_directory

ROOT = Path(__file__).resolve().parent
COLUMNS = ['chronologic_id', 'hash_key', 'text', 'labels', 'edited']
app = Flask(__name__)
lock = threading.RLock()
config = {}
records = {}
texts = []
database = pd.DataFrame(columns=COLUMNS)
status = {}
generation = 0
revision = 0
active_key = None


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# Require a semicolon so ordinary text such as "AT&T" stays untouched.
ENTITY = re.compile(r'&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);')


def decode_text(text, labels=None):
    """Canonicalize entities and translate saved Unicode span boundaries."""
    labels = [list(item) for item in (labels or [])]
    while True:
        pieces, boundaries, cursor, size = [], {0: 0}, 0, 0
        for match in ENTITY.finditer(text):
            replacement = html.unescape(match.group())
            if replacement == match.group():
                continue
            for index in range(cursor, match.start() + 1):
                boundaries[index] = size + index - cursor
            pieces.append(text[cursor:match.start()])
            size += match.start() - cursor
            pieces.append(replacement)
            size += len(replacement)
            cursor = match.end()
            boundaries[cursor] = size
        if cursor == 0:
            return text, labels
        for index in range(cursor, len(text) + 1):
            boundaries[index] = size + index - cursor
        pieces.append(text[cursor:])
        for item in labels:
            if item[0] not in boundaries or item[1] not in boundaries:
                raise ValueError('A saved annotation ends inside an HTML entity; correct its boundaries before importing.')
            item[0], item[1] = boundaries[item[0]], boundaries[item[1]]
        text = ''.join(pieces)
        validate_labels(labels, text)


def load_input(value):
    if not value:
        return []
    # TXT is line based: commas and quotes are literal characters, not delimiters.
    return [decode_text(line)[0] for line in path_for(value).read_text(
        encoding='utf-8-sig').splitlines() if line.strip()]


def path_for(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def validate_labels(labels, text, names=None):
    if not isinstance(labels, list):
        raise ValueError('Labels must be an array.')
    result = []
    for item in labels:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError('Each label needs start, end and short name.')
        start = item[0]
        end = item[1]
        name = item[2]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError('Invalid span offsets.')
        if not isinstance(name, str) or (names is not None and name not in names):
            raise ValueError(f'Unknown label name: {name!r}. Add it to the config or update the inference server labels.')
        result.append([start, end, name])
    result.sort(key=lambda item: item[0])
    if any(a[1] > b[0] for a, b in zip(result, result[1:])):
        raise ValueError('Annotations cannot overlap. Remove the existing span first.')
    return result


def prediction_labels(labels, text, mapping):
    """Normalize endpoint names and ignore labels outside the configured schema."""
    if not isinstance(labels, list):
        raise ValueError('Labels must be an array.')
    recognized = []
    for item in labels:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError('Each label needs start, end and short name.')
        name = item[2]
        if not isinstance(name, str):
            raise ValueError('Label names must be strings.')
        if name in mapping:
            recognized.append([item[0], item[1], mapping[name]])
    return validate_labels(recognized, text, set(mapping.values()))


def read_config(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    labels = value.get('labels', [])
    if not labels:
        raise ValueError('Configure at least one label.')
    names, ids = set(), set()
    for label in labels:
        name, color, ident = label['short_name'], label['color'], label['id']
        if not isinstance(name, str) or not name or name in names or type(ident) is not int or ident in ids:
            raise ValueError('Label names and integer IDs must be unique.')
        if not isinstance(color, str) or len(color) != 7 or color[0] != '#':
            raise ValueError('Label colors must use #RRGGBB.')
        int(color[1:], 16)
        names.add(name)
        ids.add(ident)
    owners = {name: name for name in names}
    for label in labels:
        aliases = label.setdefault('aliases', [])
        if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias for alias in aliases):
            raise ValueError('Label aliases must be a list of nonempty strings.')
        for alias in aliases:
            if alias in owners and owners[alias] != label['short_name']:
                raise ValueError(f'Ambiguous label alias: {alias!r}.')
            owners[alias] = label['short_name']
    settings = value.setdefault('settings', {})
    for key in ['input_path', 'database_path', 'inference_url']:
        settings.setdefault(key, '')
    settings['config_path'] = str(path.resolve())
    return value


def write_config(value):
    atomic_write(path_for(value['settings']['config_path']), json.dumps(value, indent=2) + '\n')


def atomic_write(path, content):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    os.replace(temporary, path)


def load_database(value):
    if not value:
        return pd.DataFrame(columns=COLUMNS)
    path = path_for(value)
    if not path.exists():
        if not path.parent.is_dir():
            raise ValueError('Database directory does not exist.')
        return pd.DataFrame(columns=COLUMNS)
    with path.open(encoding='utf-8-sig', newline='') as source:
        rows = list(csv.reader(source, strict=True))
    if not rows or any(len(row) != len(COLUMNS) for row in rows):
        raise ValueError('Invalid CSV row: quote fields containing commas, quotes or line breaks.')
    frame = pd.DataFrame(rows[1:], columns=rows[0])
    if list(frame.columns) != COLUMNS:
        raise ValueError('CSV columns must be: ' + ', '.join(COLUMNS))
    for index, row in frame.iterrows():
        int(row['chronologic_id'])
        if row['hash_key'] != text_hash(row['text']):
            raise ValueError('CSV hash does not match its text.')
        labels = validate_labels(json.loads(row['labels']), row['text'])
        text, labels = decode_text(row['text'], labels)
        frame.at[index, 'text'] = text
        frame.at[index, 'hash_key'] = text_hash(text)
        frame.at[index, 'labels'] = json.dumps(labels, ensure_ascii=False)
        if str(row['edited']).lower() not in ['true', 'false']:
            raise ValueError('CSV edited values must be true or false.')
    if frame['hash_key'].duplicated().any() or frame['chronologic_id'].duplicated().any():
        raise ValueError('CSV hashes and chronological IDs must be unique, including after HTML entity decoding.')
    return frame


def write_database(frame):
    if status.get('database_path', ['off'])[0] == 'error':
        raise ValueError('Fix the database path before saving; the existing file could not be loaded.')
    value = config['settings']['database_path']
    if not value:
        raise ValueError('Set a CSV database path first.')
    atomic_write(path_for(value), frame.to_csv(index=False, quoting=csv.QUOTE_ALL, lineterminator='\n'))


def make_record(text, edited=False):
    key = text_hash(text)
    rows = database[database.hash_key == key]
    saved = not rows.empty
    return dict(text=text, hash_key=key,
                labels=json.loads(rows.iloc[0]['labels']) if saved else [],
                saved=saved, dirty=False,
                edited=(str(rows.iloc[0]['edited']).lower() == 'true') if saved else edited,
                prediction='saved' if saved else 'idle', touched=False)


def reload_texts(lines):
    global texts, records, generation
    generation += 1
    texts = [text_hash(line) for line in lines]
    records = {text_hash(line): make_record(line) for line in lines}


def reset_prediction():
    """Invalidate old requests after settings change; wait for an active text."""
    global generation, revision, active_key
    generation += 1
    revision += 1
    active_key = None
    for record in records.values():
        if not record['saved'] and not record['touched']:
            record.update(labels=[], prediction='idle', prediction_error='')
    status['inference_url'] = ['off', 'Ready for the active text.' if config['settings']['inference_url'] else 'No inference endpoint configured.']


def start_prediction(key, retry=False):
    """Called under the lock. Only the displayed text gets a prediction."""
    global generation, active_key
    if key == active_key and not retry:
        return
    previous = records.get(active_key)
    if previous and not previous['saved'] and not previous['touched']:
        previous.update(labels=[], prediction='idle', prediction_error='')
    generation += 1
    version = generation
    active_key = key
    record = records[key]
    url = config['settings']['inference_url']
    status['inference_url'] = ['off', 'No prediction needed for this text.']
    if not url:
        status['inference_url'] = ['off', 'No inference endpoint configured.']
        return
    if record['saved'] or record['touched']:
        return
    text = record['text']
    mapping = {name: label['short_name'] for label in config['labels']
               for name in [label['short_name'], *label.get('aliases', [])]}
    record.update(prediction='pending', prediction_error='')
    status['inference_url'] = ['busy', 'Predicting the active text…']

    def worker():
        try:
            req = urllib.request.Request(url, data=json.dumps({'text': text}).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as response:
                labels = prediction_labels(json.loads(response.read())['labels'], text, mapping)
            error = ''
        except Exception as exc:
            labels, error = [], str(exc)
        with lock:
            # A request already sent cannot be cancelled, but its result can be ignored.
            if version != generation or active_key != key:
                return
            current = records.get(key)
            if current is not record or record['saved'] or record['touched']:
                status['inference_url'] = ['off', 'Manual changes kept; prediction ignored.']
                return
            record.update(labels=labels, prediction='error' if error else 'ready', prediction_error=error)
            status['inference_url'] = ['error', 'Prediction failed: ' + error] if error else ['ok', 'Prediction loaded for the active text.']
    threading.Thread(target=worker, daemon=True).start()


@app.post('/api/text/<int:index>/activate')
def activate_text(index):
    data = request.get_json()
    with lock:
        if not 0 <= index < len(texts) or data.get('hash_key') != texts[index] or data.get('revision') != revision:
            raise ValueError('The input changed. Please refresh and try again.')
        start_prediction(texts[index], retry=data.get('retry') is True)
    return jsonify(ok=True)


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/icons/<path:name>')
def icon(name):
    return send_from_directory(ROOT / 'icons', name)


@app.get('/api/state')
def state():
    with lock:
        return jsonify(config=config, status=status, revision=revision, texts=[records[key] for key in texts])


@app.post('/api/settings')
def settings():
    global config, database
    data = request.get_json()
    key, value = data['key'], data['value'].strip()
    if key not in ['config_path', 'input_path', 'database_path', 'inference_url']:
        raise ValueError('Unknown setting.')
    with lock:
        candidate = json.loads(json.dumps(config))
        if key == 'config_path':
            candidate = read_config(path_for(value))
        else:
            candidate['settings'][key] = value
        endpoint = candidate['settings']['inference_url']
        if endpoint and (urlparse(endpoint).scheme not in ['http', 'https'] or not urlparse(endpoint).netloc):
            raise ValueError('Use a complete http:// or https:// endpoint URL.')
        new_db = load_database(candidate['settings']['database_path'])
        lines = None
        if key in ['config_path', 'input_path']:
            source = candidate['settings']['input_path']
            lines = load_input(source)
        write_config(candidate)
        config, database = candidate, new_db
        if lines is not None:
            reload_texts(lines)
        elif key == 'database_path':
            reload_texts([records[item]['text'] for item in texts])
        for field in ['config_path', 'input_path', 'database_path']:
            status[field] = ['ok' if config['settings'][field] else 'off', 'Loaded / ready' if config['settings'][field] else 'Not configured']
        reset_prediction()
    return jsonify(ok=True)


@app.post('/api/text/<int:index>')
def update_text(index):
    global database
    data = request.get_json()
    with lock:
        if not 0 <= index < len(texts):
            raise ValueError('Text is no longer available.')
        key = texts[index]
        if data.get('hash_key') != key:
            raise ValueError('The active text changed. Please try again.')
        record = records[key]
        action = data['action']
        if action == 'labels':
            record['labels'] = validate_labels(data['labels'], record['text'], {label['short_name'] for label in config['labels']})
            record.update(touched=True, dirty=True, edited=True, prediction='manual')
        elif action == 'save':
            frame = database[database.hash_key != key].copy()
            old = database[database.hash_key == key]
            ident = int(old.iloc[0].chronologic_id) if not old.empty else (max([int(n) for n in database.chronologic_id], default=0) + 1)
            row = [ident, key, record['text'], json.dumps(record['labels']), record['edited']]
            frame = pd.concat([frame, pd.DataFrame([row], columns=COLUMNS)], ignore_index=True)
            write_database(frame)
            database = frame
            record.update(saved=True, dirty=False, touched=True)
        elif action == 'remove':
            frame = database[database.hash_key != key].copy()
            write_database(frame)
            database = frame
            record.update(saved=False, touched=True, dirty=False)
        elif action == 'edit':
            text = data['text']
            if not isinstance(text, str) or not text.strip():
                raise ValueError('Text cannot be empty.')
            text, _ = decode_text(text)
            if text == record['text']:
                return jsonify(ok=True)
            new_key = text_hash(text)
            if new_key in records or new_key in database.hash_key.values:
                raise ValueError('That text already exists. Choose a different text to avoid replacing its annotations.')
            if record['saved']:
                frame = database[database.hash_key != key].copy()
                write_database(frame)
                database = frame
            # Identical source lines share the same record and are edited together.
            texts[:] = [new_key if item == key else item for item in texts]
            del records[key]
            records[new_key] = make_record(text, edited=True)
            records[new_key].update(touched=True, prediction='manual')
        else:
            raise ValueError('Unknown action.')
    return jsonify(ok=True)


@app.errorhandler(Exception)
def error_response(error):
    return jsonify(error=str(error)), getattr(error, 'code', 400)


def initialize(config_path=None):
    global config, database
    config = read_config(path_for(config_path or os.environ.get('NER_CONFIG', 'config.json')))
    for key in ['config_path', 'input_path', 'database_path']:
        status[key] = ['ok' if config['settings'][key] else 'off', 'Loaded / ready' if config['settings'][key] else 'Not configured']
    try:
        database = load_database(config['settings']['database_path'])
    except Exception as exc:
        database = pd.DataFrame(columns=COLUMNS)
        status['database_path'] = ['error', str(exc)]
    try:
        source = config['settings']['input_path']
        reload_texts(load_input(source))
    except Exception as exc:
        reload_texts([])
        status['input_path'] = ['error', str(exc)]
    reset_prediction()


if __name__ == '__main__':
    initialize()
    app.run(host='127.0.0.1', port=5000, debug=False)
