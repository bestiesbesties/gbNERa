import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import app as editor


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.txt'
        self.source.write_text('😀 Contact john@example.com\nSecond text\n', encoding='utf-8')
        self.csv = self.root / 'db.csv'
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'labels': [{'id': 1, 'short_name': 'MAIL', 'color': '#00cc77'}], 'settings': {'input_path': str(self.source), 'database_path': str(self.csv), 'inference_url': ''}}))
        editor.initialize(str(self.config))
        self.client = editor.app.test_client()

    def tearDown(self):
        with editor.lock:
            editor.generation += 1
        self.temp.cleanup()

    def record(self):
        return self.client.get('/api/state').json['texts'][0]

    def change(self, action, **data):
        return self.client.post('/api/text/0', json={'action': action, 'hash_key': self.record()['hash_key'], **data})

    def activate(self, index, retry=False):
        state = self.client.get('/api/state').json
        response = self.client.post(f'/api/text/{index}/activate', json={
            'hash_key': state['texts'][index]['hash_key'],
            'revision': state['revision'], 'retry': retry})
        self.assertEqual(response.status_code, 200)

    def wait_prediction(self):
        for _ in range(200):
            if editor.status['inference_url'][0] != 'busy':
                return
            time.sleep(.01)
        self.fail('Prediction did not finish')

    def test_explicit_save_edit_and_reload(self):
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.change('labels', labels=[[10, 26, 'MAIL']]).status_code, 200)
        self.assertFalse(self.csv.exists())
        self.change('save')
        editor.initialize(str(self.config))
        self.assertTrue(self.record()['saved'])
        self.assertEqual(self.record()['labels'], [[10, 26, 'MAIL']])
        self.change('labels', labels=[])
        self.assertEqual(json.loads(editor.load_database(str(self.csv)).iloc[0]['labels']), [[10, 26, 'MAIL']])
        old_hash = self.record()['hash_key']
        self.change('edit', text='A changed sentence')
        self.assertNotEqual(self.record()['hash_key'], old_hash)
        self.assertFalse(self.record()['saved'])
        self.assertEqual(self.record()['labels'], [])
        self.assertTrue(editor.load_database(str(self.csv)).empty)
        self.change('save')
        self.assertTrue(self.record()['edited'])
        self.change('remove')
        self.assertTrue(editor.load_database(str(self.csv)).empty)

    def test_invalid_database_is_not_overwritten(self):
        self.csv.write_text('invalid,csv\n1,2\n')
        editor.initialize(str(self.config))
        self.assertEqual(self.change('save').status_code, 400)
        self.assertEqual(self.csv.read_text(), 'invalid,csv\n1,2\n')

    def test_page_and_assets(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertIn(b'Annotation editor', self.client.get('/').data)
        for path in ['/static/app.js', '/static/style.css', '/icons/edit.png']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_overlap_and_offsets(self):
        self.assertEqual(self.change('labels', labels=[[0, 4, 'MAIL'], [3, 5, 'MAIL']]).status_code, 400)
        self.assertEqual(self.change('labels', labels=[[0, 99, 'MAIL']]).status_code, 400)
        self.assertEqual(self.change('labels', labels=[[0, 1, 'MAIL']]).status_code, 200)
        self.assertEqual(self.record()['text'][0:1], '😀')

    def test_failed_settings_keep_current_data(self):
        response = self.client.post('/api/settings', json={'key': 'input_path', 'value': str(self.root / 'missing.txt')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(editor.texts), 2)
        self.assertEqual(editor.config['settings']['input_path'], str(self.source))

    def test_inference_does_not_overwrite_manual_work(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"labels": [[0, 1, "MAIL"]]}'
        def predict(req, timeout):
            self.assertEqual(set(json.loads(req.data)), {'text'})
            started.set()
            release.wait(3)
            finished.set()
            return Response()
        with patch('app.urllib.request.urlopen', predict):
            self.client.post('/api/settings', json={'key': 'inference_url', 'value': 'http://localhost:8080/predict'})
            self.activate(0)
            self.assertTrue(started.wait(1))
            self.change('labels', labels=[[2, 9, 'MAIL']])
            release.set()
            self.assertTrue(finished.wait(1))
            for _ in range(100):
                if editor.status['inference_url'][0] != 'busy': break
                time.sleep(.01)
        self.assertEqual(self.record()['labels'], [[2, 9, 'MAIL']])
        self.assertEqual(editor.records[editor.texts[1]]['prediction'], 'idle')
        self.assertFalse(self.csv.exists())

    def test_saved_text_skips_inference(self):
        self.change('save')
        called = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"labels": []}'
        def predict(req, timeout):
            called.append(json.loads(req.data)['text'])
            return Response()
        with patch('app.urllib.request.urlopen', predict):
            self.client.post('/api/settings', json={'key': 'inference_url', 'value': 'http://localhost:8080/predict'})
            self.assertEqual(called, [])
            self.activate(0)
            self.assertEqual(called, [])
            self.activate(1)
            self.wait_prediction()
        self.assertEqual(called, ['Second text'])

    def test_only_active_text_and_no_prediction_cache(self):
        called = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"labels": [[0, 1, "MAIL"]]}'
        def predict(req, timeout):
            called.append(json.loads(req.data)['text'])
            return Response()
        with patch('app.urllib.request.urlopen', predict):
            self.client.post('/api/settings', json={'key': 'inference_url', 'value': 'http://localhost:8081/inference'})
            self.assertEqual(called, [])
            self.activate(0)
            self.wait_prediction()
            self.activate(0)
            self.assertEqual(len(called), 1)
            self.activate(1)
            self.wait_prediction()
            self.assertEqual(self.record()['labels'], [])
            self.activate(0)
            self.wait_prediction()
            self.assertEqual(len(called), 3)
            self.assertEqual(editor.records[editor.texts[1]]['labels'], [])
        self.assertFalse(self.csv.exists())

    def test_late_response_after_navigation_is_ignored(self):
        started, release = threading.Event(), threading.Event()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"labels": [[0, 1, "MAIL"]]}'
        def predict(req, timeout):
            if json.loads(req.data)['text'].startswith('😀'):
                started.set()
                release.wait(2)
            return Response()
        with patch('app.urllib.request.urlopen', predict):
            self.client.post('/api/settings', json={'key': 'inference_url', 'value': 'http://localhost:8081/inference'})
            self.activate(0)
            self.assertTrue(started.wait(1))
            self.activate(1)
            self.wait_prediction()
            release.set()
            time.sleep(.03)
        self.assertEqual(self.record()['prediction'], 'idle')
        self.assertEqual(self.record()['labels'], [])
        self.assertEqual(editor.records[editor.texts[1]]['prediction'], 'ready')

    def test_unknown_labels_are_ignored(self):
        class Response:
            body = b'{"labels": [[0, 1, "ORG"]]}'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self.body
        with patch('app.urllib.request.urlopen', return_value=Response()):
            self.client.post('/api/settings', json={'key': 'inference_url', 'value': 'http://localhost:8081/inference'})
            self.activate(0)
            self.wait_prediction()
            self.assertEqual(self.record()['prediction'], 'ready')
            self.assertEqual(self.record()['labels'], [])

    def test_aliases_and_unknown_spans(self):
        mapping = {'NAME': 'NAME', 'PER': 'NAME'}
        self.assertEqual(editor.prediction_labels(
            [[0, 4, 'PER'], [0, 99, 'ORG'], [5, 9, 'NAME']],
            'John Jane', mapping), [[0, 4, 'NAME'], [5, 9, 'NAME']])
        with self.assertRaises(ValueError):
            editor.prediction_labels([[0, 99, 'PER']], 'John', mapping)
        with self.assertRaises(ValueError):
            editor.prediction_labels([[0, 4, 'PER'], [1, 3, 'NAME']], 'John', mapping)
        self.assertEqual(self.change('labels', labels=[[0, 1, 'ORG']]).status_code, 400)

    def test_alias_config_validation(self):
        value = json.loads(self.config.read_text())
        value['labels'][0]['aliases'] = ['EMAIL']
        self.config.write_text(json.dumps(value))
        self.assertEqual(editor.read_config(self.config)['labels'][0]['aliases'], ['EMAIL'])
        for aliases in ['EMAIL', [''], [2]]:
            value['labels'][0]['aliases'] = aliases
            self.config.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                editor.read_config(self.config)
        value['labels'][0]['aliases'] = ['NAME']
        value['labels'].append({'id': 2, 'short_name': 'NAME', 'color': '#ff0000'})
        self.config.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            editor.read_config(self.config)

    def test_failed_prediction_retries_after_navigation(self):
        with patch('app.urllib.request.urlopen', side_effect=OSError('Unavailable')) as predict:
            self.client.post('/api/settings', json={'key': 'inference_url', 'value': 'http://localhost:8081/inference'})
            self.activate(0)
            self.wait_prediction()
            self.assertEqual(self.record()['prediction'], 'error')
            self.activate(1)
            self.wait_prediction()
            self.activate(0)
            self.wait_prediction()
            self.assertEqual(predict.call_count, 3)




if __name__ == '__main__':
    unittest.main()
