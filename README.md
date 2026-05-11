# Project Aegis — Fraud Detection System

A small experiment in detecting **social-engineering / scam** patterns in text using **NLP**. Today it ships one script, `feature-extractor.py`, that turns a message into a simple **risk report**.

> Educational / demo only. Output is heuristic — it can miss real scams and flag benign text.

## What `feature-extractor.py` does

Given a piece of text, it returns a dict with three signals associated with common scam scripts (impersonation → urgency → payment pivot):

- **`authority_markers`** — named entities of type `PERSON`, `ORG`, or `GPE` (e.g. *“Agent Miller”*, *“the Federal Treasury”*). Flags the **“who I claim to be”** phase.
- **`urgency_score`** — count of urgency words found (`now`, `immediately`, `urgent`, `minutes`, `seconds`, `last chance`). Flags **time pressure**.
- **`action_requests`** — payment-style asks. Picks up:
  - **Noun chunks** containing `transfer`, `pay`, `send`, `wire`, `buy`, `gift card`, `crypto` (e.g. *“a wire transfer”*).
  - **Verb lemmas** matching the same money words (e.g. *“You must wire …”*, where `wire` is a verb and not part of a noun chunk).
  - The literal phrase **`gift card`** as a safety net.

### Demo input / output

Input (built into the script):

> *"This is Agent Miller from the Federal Treasury. Your account has been compromised. You must wire $5,000 via Bitcoin immediately to secure your assets."*

Sample output:

```text
Risk Report: {
  'urgency_score': 1,
  'authority_markers': ['Miller', 'the Federal Treasury', 'Bitcoin'],
  'action_requests': ['wire']
}
```

(Exact tokens depend on the spaCy model version.)

## Tech used

- **Python 3.9+** (tested on macOS / Python 3.9).
- **[spaCy](https://spacy.io/)** `>=3.7.2,<3.8` — tokenization, **POS tagging**, **named entity recognition (NER)**, **noun-chunk extraction**.
- **spaCy English model** `en_core_web_sm` — small pretrained pipeline (a larger model like `en_core_web_trf` would improve NER quality).

No web framework, database, or ML training step — it’s a pure-CLI text analyzer.

## Requirements

- **Python ≥ 3.9**
- The packages in [`requirements.txt`](./requirements.txt)
- The spaCy model **`en_core_web_sm`** (downloaded separately; it isn't on PyPI as a normal package)

> **Why pinned to spaCy 3.7.x?** spaCy 3.8+ depends on `thinc>=8.3.12`, which requires **Python ≥ 3.10**. On Python 3.9 the install fails with *“No matching distribution found for thinc”*. Pinning `spacy<3.8` keeps Python 3.9 working. If you upgrade to Python 3.10+, you can move to a newer spaCy.

## Setup and run

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python feature-extractor.py
```

Expected: a `Risk Report: {...}` line printed to stdout.

You may see a macOS warning like  
`urllib3 v2 only supports OpenSSL 1.1.1+, currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'`  
That's noise from a transitive dependency — it does **not** stop the script.

## Using your own text

Edit the bottom of `feature-extractor.py`:

```python
scam_message = "Your text here..."
analysis = analyze_social_engineering(scam_message)
print(f"Risk Report: {analysis}")
```

Or import it from another script:

```python
from importlib import import_module
fe = import_module("feature-extractor")
print(fe.analyze_social_engineering("call me back at this number now"))
```

(The module name has a dash, so `importlib` is the easy way.)

## Limitations

- **Small NER model** mislabels things (e.g. *Bitcoin* may show up as an authority marker).
- **Urgency** is a fixed keyword list, not real semantics.
- **No language detection** — built for English.
- **Single message at a time** — no batching, scoring threshold, or persistence.

## Project layout

- `feature-extractor.py` — the analyzer + a demo run.
- `requirements.txt` — pinned spaCy compatible with Python 3.9.
- `.gitignore` — keeps the local `.venv` out of Git.
