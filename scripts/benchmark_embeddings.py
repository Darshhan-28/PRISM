"""Benchmark BGE-small vs MiniLM via FastEmbed — RAM, load time, speed, retrieval quality.

CPU-only, offline after download. Run on target laptop (16 GB, Iris Xe).
Outputs to docs/BENCHMARK_EMBEDDINGS.md
"""
import time
import sys
import pathlib
import psutil
import tempfile
import hashlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastembed import TextEmbedding

MODELS = [
    "BAAI/bge-small-en-v1.5",
    "sentence-transformers/all-MiniLM-L6-v2",
]

# Synthetic corpus (6 chunks from samples + expanded)
CORPUS = [
    "Standard Operating Procedure Pump P-204 Normal pressure 2.1 to 3.4 bar inspection interval 30 days shutdown procedure section 4.2",
    "Maintenance log P-204 valve replacement on 12 August by Tech B seal inspection on 10 August",
    "Sensor data P-204 pressure 4.8 bar at 14:30 on 2026-08-15 exceeds threshold 4.9 bar at 15:00",
    "Operator note abnormal vibration observed in Pump P-204 at 14:35 loud noise supervisor notified",
    "System events WARN P-204 pressure 4.8 bar EXCEEDS THRESHOLD vibration high auto-shutdown triggered",
    "Checklist Pump P-204 pre-inspection verify gauge calibration valve replacement pending seal wear observed 2026-08-14",
    "Pump P-204 SOP requires sign-off by reliability engineer before valve replacement",
    "Pressure thresholds calibrated quarterly contact reliability at plant local",
    "Equipment P-205 normal operation pressure 2.0 bar no anomalies",
    "Inspection report valve pending replacement due to seal wear",
]

QUERIES = [
    ("normal pressure threshold P-204", 0),  # expected doc 0
    ("valve replaced August", 1),
    ("pressure spike 4.8 bar", 2),
    ("vibration observed P-204", 3),
    ("seal wear valve pending", 9),
]

def get_process_mem_mb():
    proc = psutil.Process()
    return proc.memory_info().rss / 1024 / 1024

def cosine(a, b):
    import math
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    return dot / (na*nb) if na and nb else 0.0

def benchmark_model(model_name: str):
    print(f"\n=== Benchmarking {model_name} ===")
    mem_before = get_process_mem_mb()
    t0 = time.time()
    model = TextEmbedding(model_name=model_name)
    load_time = time.time() - t0
    mem_after = get_process_mem_mb()
    mem_delta = mem_after - mem_before

    # Warmup
    list(model.embed(["warmup text"]))

    # Embedding speed: batch of corpus
    t1 = time.time()
    embs = list(model.embed(CORPUS))
    t_embed = time.time() - t1
    # Convert to lists
    embs_list = [e.tolist() if hasattr(e, "tolist") else list(e) for e in embs]
    dim = len(embs_list[0])
    print(f"dim={dim} load_time={load_time:.2f}s mem_delta={mem_delta:.1f} MB batch_time={t_embed:.2f}s for {len(CORPUS)} docs")

    # Single query latency
    t2 = time.time()
    for _ in range(5):
        list(model.embed(["single query test"]))
    single_avg = (time.time() - t2) / 5
    print(f"single_query_avg={single_avg*1000:.1f} ms")

    # Retrieval quality
    correct = 0
    for q, expected_idx in QUERIES:
        q_emb = list(model.embed([q]))[0]
        q_emb = q_emb.tolist() if hasattr(q_emb, "tolist") else list(q_emb)
        scores = [cosine(q_emb, e) for e in embs_list]
        # rank
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        top1 = ranked[0]
        top3 = ranked[:3]
        hit_top1 = (top1 == expected_idx)
        hit_top3 = (expected_idx in top3)
        if hit_top1:
            correct += 1
        print(f"  Q: '{q}' -> top1={top1} (expected {expected_idx}) scores top={scores[top1]:.3f} hit_top1={hit_top1} hit_top3={hit_top3}")

    accuracy_top1 = correct / len(QUERIES)
    print(f"accuracy_top1={accuracy_top1:.2f}")

    # Model size estimate via cache
    from pathlib import Path
    cache = Path.home() / ".cache" / "fastembed"
    # Approximate
    print(f"cache_dir={cache} exists={cache.exists()}")

    return {
        "model": model_name,
        "dim": dim,
        "load_time_s": round(load_time, 2),
        "mem_delta_mb": round(mem_delta, 1),
        "batch_time_s": round(t_embed, 2),
        "single_ms": round(single_avg*1000, 1),
        "accuracy_top1": round(accuracy_top1, 2),
    }

results = []
for m in MODELS:
    try:
        r = benchmark_model(m)
        results.append(r)
    except Exception as e:
        print(f"FAILED {m}: {e}")
        import traceback; traceback.print_exc()

# Summary
print("\n=== SUMMARY ===")
for r in results:
    print(r)

# Write markdown
out = pathlib.Path("docs/BENCHMARK_EMBEDDINGS.md")
out.parent.mkdir(exist_ok=True)
with out.open("w", encoding="utf-8") as f:
    f.write("# BENCHMARK — Embeddings (FastEmbed, CPU-only)\n\n")
    f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    f.write(f"Machine: Windows 11, Intel 12th-gen mobile, 16 GB RAM, Iris Xe, Python 3.13.2\n\n")
    f.write("| Model | Dim | Load (s) | Mem Δ (MB) | Batch 10 (s) | Single (ms) | Top-1 Acc |\n")
    f.write("|---|---|---|---|---|---|---|\n")
    for r in results:
        f.write(f"| {r['model']} | {r['dim']} | {r['load_time_s']} | {r['mem_delta_mb']} | {r['batch_time_s']} | {r['single_ms']} | {r['accuracy_top1']} |\n")
    f.write("\n")
    f.write("## Notes\n")
    f.write("- Batch = 10 corpus chunks (synthetic industrial).\n")
    f.write("- Queries = 5 synthetic queries with expected top-1 (see script).\n")
    f.write("- Offline after download; models cached under ~/.cache/fastembed.\n")
    f.write("- Recommendation below is auto-generated; verify on target laptop.\n")
    if results:
        best = max(results, key=lambda x: x["accuracy_top1"])
        # tie break by load time / mem
        candidates = [r for r in results if r["accuracy_top1"] == best["accuracy_top1"]]
        best = min(candidates, key=lambda x: x["load_time_s"])
        f.write(f"\n## Recommendation\nSelected: **{best['model']}** (highest accuracy, lowest load time on tie).\n")
print(f"Wrote {out}")
