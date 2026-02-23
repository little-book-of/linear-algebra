#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "books" / "en-US"
DEFAULT = [ROOT / "index.qmd", SRC / "book.qmd", SRC / "lab.qmd"]
PROTECTED_INLINE = re.compile(r"(`[^`]*`|\$[^$]*\$)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="zh-CN")
    parser.add_argument(
        "--files",
        nargs="*",
        default=[str(x.relative_to(ROOT)) for x in DEFAULT],
    )
    parser.add_argument("--chunk-size", type=int, default=1800)
    parser.add_argument("--chunk-timeout-seconds", type=int, default=20)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--checkpoint-dir", default=".translation_checkpoints")
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=0,
        help="Translate only this many untranslated chunks per file (0 = all).",
    )
    parser.add_argument("--log-every-chunk", action="store_true")
    return parser.parse_args()


def destination_for(src: Path, lang: str) -> Path:
    if src == ROOT / "index.qmd":
        return ROOT / "books" / lang / "index.qmd"
    return ROOT / "books" / lang / src.relative_to(SRC)


def checkpoint_path(checkpoint_dir: Path, src: Path, target_lang: str) -> Path:
    slug = str(src.relative_to(ROOT)).replace("/", "__")
    return checkpoint_dir / target_lang / f"{slug}.json"


def split_chunks(text: str, chunk_size: int) -> list[str]:
    i = 0
    chunks: list[str] = []
    while i < len(text):
        j = min(len(text), i + chunk_size)
        if j < len(text):
            k = text.rfind("\n", i, j)
            if k > i + 100:
                j = k + 1
        chunks.append(text[i:j])
        i = j
    return chunks


def is_inline_protected(part: str) -> bool:
    return (part.startswith("`") and part.endswith("`")) or (
        part.startswith("$") and part.endswith("$")
    )


def build_jobs(text: str, chunk_size: int) -> tuple[list[dict], list[dict]]:
    """Return segments + flat chunk jobs for full file."""
    segments: list[dict] = []
    jobs: list[dict] = []
    in_code = False
    in_math = False
    in_frontmatter = False

    for line_no, line in enumerate(text.splitlines(True)):
        stripped = line.strip()
        if line_no == 0 and stripped == "---":
            in_frontmatter = True
            segments.append({"kind": "raw", "text": line})
            continue
        if in_frontmatter:
            segments.append({"kind": "raw", "text": line})
            if stripped == "---":
                in_frontmatter = False
            continue

        if stripped.startswith("```"):
            in_code = not in_code
            segments.append({"kind": "raw", "text": line})
            continue
        if stripped == "$$":
            in_math = not in_math
            segments.append({"kind": "raw", "text": line})
            continue
        if in_code or in_math:
            segments.append({"kind": "raw", "text": line})
            continue

        # normal text line: split into protected and translatable spans
        line_parts = PROTECTED_INLINE.split(line)
        line_segment: list[dict] = []
        for part in line_parts:
            if not part:
                continue
            if is_inline_protected(part) or not part.strip():
                line_segment.append({"kind": "raw", "text": part})
                continue

            piece_chunks = split_chunks(part, chunk_size)
            chunk_ids = []
            for chunk in piece_chunks:
                chunk_id = len(jobs)
                chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                jobs.append({"id": chunk_id, "source": chunk, "hash": chunk_hash})
                chunk_ids.append(chunk_id)
            line_segment.append({"kind": "chunks", "ids": chunk_ids})

        segments.append({"kind": "composite", "parts": line_segment})

    return segments, jobs


def load_checkpoint(path: Path, source_hash: str) -> dict:
    if not path.exists():
        return {"source_hash": source_hash, "translations": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"source_hash": source_hash, "translations": {}}

    if data.get("source_hash") != source_hash:
        return {"source_hash": source_hash, "translations": {}}
    return {"source_hash": source_hash, "translations": data.get("translations", {})}


def save_checkpoint(path: Path, source_hash: str, translations: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "source_hash": source_hash,
        "updated_at": int(time.time()),
        "translations": translations,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def looks_untranslated(src: str, translated: str) -> bool:
    src_clean = src.strip()
    dst_clean = translated.strip()
    return bool(src_clean and src_clean == dst_clean and re.search(r"[A-Za-z]", src_clean))


def preserve_edge_whitespace(source: str, translated: str) -> str:
    lead = len(source) - len(source.lstrip())
    trail = len(source) - len(source.rstrip())
    core = translated.strip() if translated.strip() else translated
    return source[:lead] + core + (source[len(source)-trail:] if trail else "")


def translate_one_chunk(
    text: str,
    source_lang: str,
    target_lang: str,
    timeout_seconds: int,
    max_retries: int,
) -> tuple[str, bool, int, str]:
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            translated = translator.translate(text, timeout=timeout_seconds) or text
            translated = preserve_edge_whitespace(text, translated)
            if looks_untranslated(text, translated):
                last_err = "translation identical to source"
                continue
            return translated, True, attempt, ""
        except Exception as exc:
            last_err = str(exc)
            time.sleep(1)
    return text, False, max_retries, last_err


def render_output(segments: list[dict], resolved: dict[int, str]) -> str:
    out: list[str] = []
    for seg in segments:
        if seg["kind"] == "raw":
            out.append(seg["text"])
            continue
        for part in seg["parts"]:
            if part["kind"] == "raw":
                out.append(part["text"])
            else:
                for cid in part["ids"]:
                    out.append(resolved[cid])
    return "".join(out)


def paragraph_blocks(text: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return [b for b in blocks if not b.startswith("```") and b != "$$" and not re.fullmatch(r"#+\s+.*", b)]


def verify_translation(src_text: str, dst_text: str) -> tuple[int, int]:
    src_blocks = paragraph_blocks(src_text)
    dst_blocks = paragraph_blocks(dst_text)
    total = max(len(src_blocks), len(dst_blocks))
    untranslated = 0
    for i in range(min(len(src_blocks), len(dst_blocks))):
        if looks_untranslated(src_blocks[i], dst_blocks[i]):
            untranslated += 1
    untranslated += abs(len(src_blocks) - len(dst_blocks))
    return total, untranslated


def translate_file(
    src: Path,
    dst: Path,
    args: argparse.Namespace,
) -> tuple[int, int, int, int]:
    src_text = src.read_text(encoding="utf-8")
    source_hash = hashlib.sha256(src_text.encode("utf-8")).hexdigest()
    cp_path = checkpoint_path(ROOT / args.checkpoint_dir, src, args.target_lang)
    checkpoint = load_checkpoint(cp_path, source_hash)
    translations = checkpoint["translations"]

    segments, jobs = build_jobs(src_text, args.chunk_size)
    total_jobs = len(jobs)

    pending_all: list[dict] = []
    cached_count = 0
    for job in jobs:
        cached = translations.get(str(job["id"]))
        if cached and cached.get("hash") == job["hash"] and isinstance(cached.get("text"), str):
            cached_count += 1
            continue
        pending_all.append(job)

    pending = pending_all
    if args.max_chunks > 0:
        pending = pending_all[: args.max_chunks]
    deferred_count = len(pending_all) - len(pending)

    completed = 0
    fallback = 0
    start = time.time()

    def log_progress(status: str, chunk_id: int) -> None:
        if not args.log_every_chunk:
            return
        run_total = max(len(pending), 1)
        run_done = completed
        run_pct = (run_done / run_total) * 100

        overall_done = cached_count + completed
        overall_pct = (overall_done / total_jobs * 100) if total_jobs else 100.0

        elapsed = max(time.time() - start, 0.001)
        rate = completed / elapsed
        run_remaining = max(len(pending) - completed, 0)
        eta = run_remaining / rate if rate > 0 else 0.0
        print(
            f"[{src.relative_to(ROOT)}] {status} chunk {chunk_id + 1}/{total_jobs} "
            f"run={run_done}/{len(pending)} ({run_pct:5.1f}%) "
            f"overall={overall_done}/{total_jobs} ({overall_pct:5.1f}%) "
            f"fallback={fallback} cached={cached_count} deferred={deferred_count} rate={rate:.2f} eta={eta:.1f}s",
            flush=True,
        )

    if args.workers <= 1:
        for job in pending:
            text, ok, attempts, err = translate_one_chunk(
                job["source"],
                args.source_lang,
                args.target_lang,
                args.chunk_timeout_seconds,
                args.max_retries,
            )
            completed += 1
            if not ok:
                fallback += 1
            translations[str(job["id"])] = {
                "hash": job["hash"],
                "text": text,
                "ok": ok,
                "attempts": attempts,
                "error": err,
            }
            save_checkpoint(cp_path, source_hash, translations)
            log_progress("done" if ok else "fallback", job["id"])
    else:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            fut_map = {
                pool.submit(
                    translate_one_chunk,
                    job["source"],
                    args.source_lang,
                    args.target_lang,
                    args.chunk_timeout_seconds,
                    args.max_retries,
                ): job
                for job in pending
            }
            for fut in as_completed(fut_map):
                job = fut_map[fut]
                text, ok, attempts, err = fut.result()
                completed += 1
                if not ok:
                    fallback += 1
                translations[str(job["id"])] = {
                    "hash": job["hash"],
                    "text": text,
                    "ok": ok,
                    "attempts": attempts,
                    "error": err,
                }
                save_checkpoint(cp_path, source_hash, translations)
                log_progress("done" if ok else "fallback", job["id"])

    # Resolve output: translated where available, source otherwise.
    resolved: dict[int, str] = {}
    for job in jobs:
        cached = translations.get(str(job["id"]))
        if cached and cached.get("hash") == job["hash"] and isinstance(cached.get("text"), str):
            resolved[job["id"]] = cached["text"]
        else:
            resolved[job["id"]] = job["source"]

    translated_text = render_output(segments, resolved)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(translated_text, encoding="utf-8")

    verified_total, untranslated = verify_translation(src_text, translated_text)
    translated_now = completed
    print(
        f"[{src.relative_to(ROOT)}] chunks: total={total_jobs}, cached={cached_count}, "
        f"translated_now={translated_now}, deferred={deferred_count}, fallback={fallback}",
        flush=True,
    )
    print(
        f"[{src.relative_to(ROOT)}] verification: untranslated paragraphs {untranslated}/{verified_total}",
        flush=True,
    )
    return total_jobs, cached_count, translated_now, fallback


def main() -> None:
    args = parse_args()
    start = time.time()

    total_jobs = 0
    total_resumed = 0
    total_translated_now = 0
    total_fallback = 0

    for index, file in enumerate(args.files, start=1):
        src = ROOT / file
        dst = destination_for(src, args.target_lang)
        print(f"[{index}/{len(args.files)}] {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}", flush=True)
        jobs, cached_count, translated_now, fallback = translate_file(src, dst, args)
        total_jobs += jobs
        total_resumed += cached_count
        total_translated_now += translated_now
        total_fallback += fallback

    print(
        f"Done in {time.time() - start:.1f}s | total_chunks={total_jobs} resumed={total_resumed} "
        f"translated_now={total_translated_now} fallback={total_fallback}",
        flush=True,
    )


if __name__ == "__main__":
    main()
