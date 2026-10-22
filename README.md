# gbNERa

A small, single-user NER labeling app using Flask, HTML, CSS and vanilla JavaScript. No Node.js or frontend build step.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Python 3.10 or newer is recommended.

## Use

Enter a path or endpoint in the livebar and press **Enter** to apply it. Paths refer to files on the computer running Flask; relative paths start in the app folder. Input files must be UTF-8, with one text per line. Empty lines are skipped. The JSONL path may name a new file in an existing directory; the file is only created on explicit save.

Select text and click a colored label button. Click an annotated span to remove it. Overlapping spans are rejected. Navigate with the arrows; manual annotation changes stay in memory during navigation. Leading and trailing whitespace is excluded when applying a label; spaces within a span are preserved. The neutral list below the text status shows the current annotations, including unsaved changes to saved texts. Untouched predictions are discarded when leaving a text.

**Right Arrow** moves to the next text, **Left Arrow** moves to the previous text, and **Enter** toggles the saved checkbox. These shortcuts pause while typing in settings fields or editing the main text. In the text editor, **Enter** applies edits and arrow keys move the cursor without navigating between texts.

The checkbox shows whether a text is in the JSONL. Checking it saves the text and its current labels; unchecking it removes the row. Changes to an already saved text's annotations stay in memory until you click **Save changes**. Predictions never save automatically.

The edit icon opens a text field. **Enter** applies the edit; **Escape** cancels it. Applying an edit removes the old saved row, clears all annotations and changes the SHA-256 hash. The changed text remains unsaved. The source TXT is not rewritten. Identical input lines share annotations and editing one updates all identical occurrences in the current session. Editing into another existing text is rejected to avoid replacing its annotations.

Reloading the input, switching the database/config or restarting the server discards unsaved work. A browser refresh keeps server memory. This is a local, single-user app; open tabs share the same session. Avoid editing the JSONL externally while the server is running.

## Configuration

`config.json` contains `labels` (unique integer `id`, `short_name`, hex `color`, and an optional `aliases` list) and `settings` (`config_path`, `input_path`, `database_path`, `inference_url`). Applied settings are written to the active config file. To start with another config:

```sh
NER_CONFIG=/path/to/config.json python app.py
```

When using another config, use that same startup command on subsequent launches. The green indicator means loaded/ready; for a new JSONL it means ready to create on save. The endpoint turns green only after successful predictions, and is gray before any requests.

## Inference

Set a full URL such as `http://127.0.0.1:8081/inference`. Only the active text is requested, in a background worker with a 15-second timeout. Importing a file does not predict the entire file. Saved texts and manually edited annotations are left alone. Navigation remains available. When you return to an untouched, unsaved text, it is predicted again; predictions are not cached across navigation. Requests already sent may finish, but results from previous texts are ignored. Only this body is sent:

```json
{"text": "Contact john@example.com"}
```

Expected response:

```json
{"labels": [[8, 24, "MAIL"]]}
```

Offsets are zero-based Unicode code-point positions with an exclusive end, matching Python slicing, including emoji. Unknown inference labels are ignored. Names match case-sensitively against each label’s `short_name` and `aliases`; for example, `"short_name": "NAME", "aliases": ["PER"]` maps `PER` predictions to `NAME`. Aliases cannot belong to multiple labels. Overlapping recognized predictions and invalid offsets are rejected. The active text shows the error; navigate away and back, or reapply the endpoint, to retry. The browser polls state only while a prediction is running and refreshes after user actions. Late responses never replace manually changed or saved labels.

## JSONL

Each line is a JSON object with fields: `hash_key`, `text`, `labels`, and `edited`. Adjacent annotations remain separate entries even when their label names match; the editor gives each its own underline and badge. Labels are native JSON arrays, for example `[[8, 24, "MAIL"]]`; `edited` is a boolean. Commas, quotes, Unicode and embedded line breaks are escaped automatically and survive round trips. Empty databases are empty files. Errors report the offending line, and invalid databases cannot be overwritten by saving.

The single `text` field contains the exact input text. No HTML entity decoding, HTML rendering, or text normalization is performed: `&amp;` stays `&amp;`. Display, editing, inference, SHA-256 hashing and annotation offsets all use this same text. TXT line separators and an initial UTF-8 BOM are excluded; whitespace within each source line is preserved. Blank input lines are skipped. The input TXT is never rewritten.

Text is displayed safely as plain text. On load, hashes, annotation offsets and uniqueness are validated. Records are held in a dictionary keyed by hash for efficient lookups. Writes atomically replace the complete file using a temporary file.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover explicit saving, reloads, edits/removals, JSONL punctuation and multiline round trips, source-text hashing and adjacent identical labels, invalid spans, failed settings and delayed background predictions. With Node.js installed, run `node --test tests/test_frontend.cjs` for selection trimming, individual span rendering/removal, and live annotation status checks.
