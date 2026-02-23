#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "books" / "en-US"
DEFAULT = [ROOT / "index.qmd", SRC / "book.qmd", SRC / "lab.qmd"]
PROTECTED = re.compile(r"(`[^`]*`|\$[^$]*\$)")


class TimeoutExc(Exception):
    pass


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
    parser.add_argument(
        "--chunk-timeout-seconds",
        type=int,
        default=20,
        help="Timeout for each translated chunk.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries per chunk before falling back to source text.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of worker threads translating chunks in parallel.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=".translation_checkpoints",
        help="Directory for JSON checkpoints that support resume.",
    )
    parser.add_argument(
        "--log-every-chunk",
        action="store_true",
        help="Emit per-chunk progress logs for detailed monitoring.",
    )
    return parser.parse_args()


def destination_for(src: Path, lang: str) -> Path:
    if src == ROOT / "index.qmd":
        return ROOT / "books" / lang / "index.qmd"
    return ROOT / "books" / lang / src.relative_to(SRC)


def split_chunks(text: str, chunk_size: int) -> list[str]:
    i = 0
    out: list[str] = []
    while i < len(text):
        j = min(len(text), i + chunk_size)
        if j < len(text):
            k = text.rfind("\n", i, j)
            if k > i + 100:
                j = k + 1
        out.append(text[i:j])
        i = j
    return out



def _timeout_handler(signum, frame):
    raise TimeoutExc()


def file_checkpoint_path(checkpoint_dir: Path, src: Path, target_lang: str) -> Path:
    slug = str(src.relative_to(ROOT)).replace("/", "__")
    return checkpoint_dir / target_lang / f"{slug}.json"


def load_checkpoint(path: Path, src_hash: str) -> dict:
    if not path.exists():
        return {"source_hash": src_hash, "chunks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"source_hash": src_hash, "chunks": {}}

    if data.get("source_hash") != src_hash:
        return {"source_hash": src_hash, "chunks": {}}
    return {"source_hash": src_hash, "chunks": data.get("chunks", {})}


def save_checkpoint(path: Path, source_hash: str, chunks: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source_hash": source_hash, "chunks": chunks, "updated_at": int(time.time())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def looks_untranslated(src: str, translated: str) -> bool:
    src_clean = src.strip()
    dst_clean = translated.strip()
    if not src_clean:
        return False
    if src_clean == dst_clean:
        return bool(re.search(r"[A-Za-z]", src_clean))
    return False


def paragraph_blocks(text: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    filtered: list[str] = []
    for block in blocks:
        if block.startswith("```") or block == "$$":
            continue
        if re.fullmatch(r"#+\s+.*", block):
            continue
        filtered.append(block)
    return filtered


def verify_translation(src_text: str, dst_text: str) -> tuple[int, int]:
    src_blocks = paragraph_blocks(src_text)
    dst_blocks = paragraph_blocks(dst_text)
    total = min(len(src_blocks), len(dst_blocks))
    untranslated = 0
    for i in range(total):
        if looks_untranslated(src_blocks[i], dst_blocks[i]):
            untranslated += 1
    # If block counts differ, treat missing as untranslated.
    untranslated += abs(len(src_blocks) - len(dst_blocks))
    total = max(len(src_blocks), len(dst_blocks))
    return total, untranslated


def translate_chunk(
    chunk: str,
    source_lang: str,
    target_lang: str,
    chunk_timeout_seconds: int,
    max_retries: int,
) -> tuple[str, bool, int, str]:
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            translated = translator.translate(chunk, timeout=chunk_timeout_seconds) or chunk
            if looks_untranslated(chunk, translated):
                last_error = "translation identical to source"
                continue
            return translated, True, attempt, ""
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    return chunk, False, max_retries, last_error


def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    chunk_size: int,
    chunk_timeout_seconds: int,
    max_retries: int,
    workers: int,
    file_label: str,
    log_every_chunk: bool,
    checkpoint_chunks: dict[str, dict],
) -> tuple[str, dict[str, dict], int, int, int]:
    parts = PROTECTED.split(text)
    out: list[str] = []

    jobs: list[tuple[int, str]] = []
    piece_meta: list[tuple[str, str, int] | None] = []
    job_index = 0

    for part in parts:
        if not part:
            piece_meta.append(None)
            continue
        if (part.startswith("`") and part.endswith("`")) or (
            part.startswith("$") and part.endswith("$")
        ):
            piece_meta.append(None)
            continue
        if not part.strip():
            piece_meta.append(None)
            continue

        chunks = split_chunks(part, chunk_size)
        piece_meta.append(("translatable", str(len(jobs)), len(chunks)))
        for chunk in chunks:
            jobs.append((job_index, chunk))
            job_index += 1

    total_chunks = len(jobs)
    if total_chunks == 0:
        return text, checkpoint_chunks, 0, 0, 0

    completed_chunks = 0
    fallback_chunks = 0
    reused_chunks = 0

    translated_map: dict[int, str] = {}
    lock = threading.Lock()
    started = time.time()

    def log_progress(status: str, idx: int):
        if not log_every_chunk:
            return
        elapsed = max(time.time() - started, 0.001)
        rate = completed_chunks / elapsed
        remaining = max(total_chunks - completed_chunks, 0)
        eta = remaining / rate if rate > 0 else 0
        pct = (completed_chunks / total_chunks) * 100
        print(
            f"[{file_label}] {status} chunk {idx + 1}/{total_chunks} "
            f"({pct:5.1f}%) | ok={completed_chunks - fallback_chunks}/{total_chunks} "
            f"fallback={fallback_chunks} resumed={reused_chunks} "
            f"rate={rate:.2f} ch/s eta={eta:.1f}s",
            flush=True,
        )

    if workers <= 1:
        for idx, chunk in jobs:
            chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            cached = checkpoint_chunks.get(str(idx))
            if cached and cached.get("chunk_hash") == chunk_hash:
                translated_map[idx] = cached.get("translated", chunk)
                completed_chunks += 1
                reused_chunks += 1
                log_progress("resume", idx)
                continue
            success = False
            translated = chunk
            attempt = 0
            error_msg = ""
            translator = GoogleTranslator(source=source_lang, target=target_lang)
            for attempt in range(1, max_retries + 1):
                try:
                    signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(chunk_timeout_seconds)
                    translated = translator.translate(chunk) or chunk
                    signal.alarm(0)
                    if looks_untranslated(chunk, translated):
                        error_msg = "translation identical to source"
                        continue
                    success = True
                    break
                except Exception as exc:
                    signal.alarm(0)
                    error_msg = str(exc)
                    time.sleep(1)
            translated_map[idx] = translated if success else chunk
            completed_chunks += 1
            if not success:
                fallback_chunks += 1
            checkpoint_chunks[str(idx)] = {
                "chunk_hash": chunk_hash,
                "translated": translated_map[idx],
                "success": success,
                "attempt": attempt,
                "error": error_msg,
            }
            log_progress("done" if success else "fallback", idx)
    else:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {}
            for idx, chunk in jobs:
                chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                cached = checkpoint_chunks.get(str(idx))
                if cached and cached.get("chunk_hash") == chunk_hash:
                    translated_map[idx] = cached.get("translated", chunk)
                    completed_chunks += 1
                    reused_chunks += 1
                    log_progress("resume", idx)
                    continue
    
                fut = pool.submit(
                    translate_chunk,
                    chunk,
                    source_lang,
                    target_lang,
                    chunk_timeout_seconds,
                    max_retries,
                )
                futures[fut] = (idx, chunk, chunk_hash)
    
            for fut in as_completed(futures):
                idx, chunk, chunk_hash = futures[fut]
                translated, success, attempt, error_msg = fut.result()
                translated_map[idx] = translated
                completed_chunks += 1
                if not success:
                    fallback_chunks += 1
                checkpoint_chunks[str(idx)] = {
                    "chunk_hash": chunk_hash,
                    "translated": translated,
                    "success": success,
                    "attempt": attempt,
                    "error": error_msg,
                }
                log_progress("done" if success else "fallback", idx)

    # rebuild content in original order
    job_ptr = 0
    for part in parts:
        if not part:
            continue
        if (part.startswith("`") and part.endswith("`")) or (
            part.startswith("$") and part.endswith("$")
        ):
            out.append(part)
            continue
        if not part.strip():
            out.append(part)
            continue

        chunks = split_chunks(part, chunk_size)
        translated_segments = []
        for _ in chunks:
            translated_segments.append(translated_map[job_ptr])
            job_ptr += 1
        out.append("".join(translated_segments))

    if log_every_chunk:
        print(
            f"[{file_label}] completed chunks: {completed_chunks}/{total_chunks}, "
            f"fallback chunks: {fallback_chunks}, resumed chunks: {reused_chunks}",
            flush=True,
        )
    return "".join(out), checkpoint_chunks, completed_chunks, fallback_chunks, reused_chunks


def translate_file(
    src: Path,
    dst: Path,
    source_lang: str,
    target_lang: str,
    chunk_size: int,
    chunk_timeout_seconds: int,
    max_retries: int,
    workers: int,
    checkpoint_dir: Path,
    log_every_chunk: bool,
) -> tuple[int, int]:
    src_text = src.read_text(encoding="utf-8")
    src_hash = hashlib.sha256(src_text.encode("utf-8")).hexdigest()
    checkpoint_path = file_checkpoint_path(checkpoint_dir, src, target_lang)
    checkpoint = load_checkpoint(checkpoint_path, src_hash)
    checkpoint_chunks = checkpoint.get("chunks", {})

    lines = src_text.splitlines(True)
    output: list[str] = []
    buffer: list[str] = []
    in_code = False
    in_math = False

    total_chunks = 0
    fallback_chunks = 0

    def flush_buffer() -> None:
        nonlocal buffer, checkpoint_chunks, total_chunks, fallback_chunks
        if not buffer:
            return
        translated, checkpoint_chunks, used_chunks, used_fallbacks, _ = translate_text(
            "".join(buffer),
            source_lang,
            target_lang,
            chunk_size,
            chunk_timeout_seconds,
            max_retries,
            workers,
            str(src.relative_to(ROOT)),
            log_every_chunk,
            checkpoint_chunks,
        )
        total_chunks += used_chunks
        fallback_chunks += used_fallbacks
        output.append(translated)
        buffer = []
        save_checkpoint(checkpoint_path, src_hash, checkpoint_chunks)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_buffer()
            in_code = not in_code
            output.append(line)
            continue
        if stripped == "$$":
            flush_buffer()
            in_math = not in_math
            output.append(line)
            continue
        if in_code or in_math:
            output.append(line)
            continue

        buffer.append(line)

    flush_buffer()
    dst.parent.mkdir(parents=True, exist_ok=True)
    translated_text = "".join(output)
    dst.write_text(translated_text, encoding="utf-8")

    verify_total, untranslated = verify_translation(src_text, translated_text)
    print(
        f"[{src.relative_to(ROOT)}] verification: untranslated paragraphs {untranslated}/{verify_total}",
        flush=True,
    )

    print(
        f"[{src.relative_to(ROOT)}] chunk summary: total={total_chunks}, fallback={fallback_chunks}",
        flush=True,
    )
    return total_chunks, fallback_chunks


def main() -> None:
    args = parse_args()

    start = time.time()
    total_chunks = 0
    total_fallbacks = 0

    for index, file in enumerate(args.files, start=1):
        src = ROOT / file
        dst = destination_for(src, args.target_lang)
        print(
            f"[{index}/{len(args.files)}] Translating {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}",
            flush=True,
        )
        file_start = time.time()
        used_chunks, used_fallbacks = translate_file(
            src,
            dst,
            args.source_lang,
            args.target_lang,
            args.chunk_size,
            args.chunk_timeout_seconds,
            args.max_retries,
            args.workers,
            ROOT / args.checkpoint_dir,
            args.log_every_chunk,
        )
        total_chunks += used_chunks
        total_fallbacks += used_fallbacks
        print(
            f"[{index}/{len(args.files)}] Finished {src.relative_to(ROOT)} in {time.time() - file_start:.1f}s",
            flush=True,
        )

    print(
        f"Translation complete for target language {args.target_lang} in {time.time() - start:.1f}s "
        f"(chunks={total_chunks}, fallback={total_fallbacks})",
        flush=True,
    )


if __name__ == "__main__":
    main()
