# NCBI / Pubmed API


## Overview

1. A one-line summary per paper:

   ```
   [2025-07-20] arXiv:2307.12345 – "Title of Paper"
   ```
2. A pretty-printed JSON block with metadata (authors, abstract snippet, PDF link).

You can watch one or more topics in parallel, with a configurable interval.

## Usage Examples

```bash
# Monitor "quantum computing" every 60s
python arxiv_monitor.py "quantum computing"

# Monitor two topics, default interval:
python arxiv_monitor.py "machine learning" "homomorphic encryption"

# Monitor with custom interval (30s):
python arxiv_monitor.py "cryptography" "lattice-based crypto" 30
```

## How it works

* Uses ArXiv's public Atom API (`/api/query`).
* Sorts by submission date, descending.
* Tracks the last seen entry per topic.
* On each poll, prints any new papers (oldest→newest) with both a summary line and detailed metadata.

No API key or token is required.
