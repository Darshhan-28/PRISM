# BENCHMARK — Embeddings (FastEmbed, CPU-only)

Date: 2026-08-28 07:29:42

Machine: Windows 11, Intel 12th-gen mobile, 16 GB RAM, Iris Xe, Python 3.13.2

| Model | Dim | Load (s) | Mem Δ (MB) | Batch 10 (s) | Single (ms) | Top-1 Acc |
|---|---|---|---|---|---|---|
| BAAI/bge-small-en-v1.5 | 384 | 13.47 | 112.8 | 0.68 | 69.0 | 0.6 |
| sentence-transformers/all-MiniLM-L6-v2 | 384 | 10.28 | 98.2 | 0.12 | 14.0 | 1.0 |

## Notes
- Batch = 10 corpus chunks (synthetic industrial).
- Queries = 5 synthetic queries with expected top-1 (see script).
- Offline after download; models cached under ~/.cache/fastembed.
- Recommendation below is auto-generated; verify on target laptop.

## Recommendation
Selected: **sentence-transformers/all-MiniLM-L6-v2** (highest accuracy, lowest load time on tie).
