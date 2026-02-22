#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import signal
import time
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


def translate_text(
    text: str,
    translator: GoogleTranslator,
    chunk_size: int,
    chunk_timeout_seconds: int,
    max_retries: int,
    file_label: str,
    log_every_chunk: bool,
) -> str:
    parts = PROTECTED.split(text)
    out: list[str] = []

    translatable_parts = [
        p
        for p in parts
        if p
        and not ((p.startswith("`") and p.endswith("`")) or (p.startswith("$") and p.endswith("$")))
        and p.strip()
    ]
    total_chunks = sum(len(split_chunks(part, chunk_size)) for part in translatable_parts)

    completed_chunks = 0
    fallback_chunks = 0

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

        translated_segments: list[str] = []
        for chunk in split_chunks(part, chunk_size):
            success = False
            for attempt in range(1, max_retries + 1):
                try:
                    signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(chunk_timeout_seconds)
                    translated_segments.append(translator.translate(chunk) or chunk)
                    success = True
                    signal.alarm(0)
                    break
                except Exception as exc:
                    signal.alarm(0)
                    if log_every_chunk:
                        print(
                            f"[{file_label}] chunk {completed_chunks + 1}/{total_chunks} "
                            f"attempt {attempt}/{max_retries} failed: {exc}",
                            flush=True,
                        )
                    time.sleep(1)

            completed_chunks += 1
            if not success:
                fallback_chunks += 1
                translated_segments.append(chunk)

            if log_every_chunk:
                status = "fallback" if not success else "ok"
                print(
                    f"[{file_label}] chunk {completed_chunks}/{total_chunks}: {status}",
                    flush=True,
                )

        out.append("".join(translated_segments))

    print(
        f"[{file_label}] completed chunks: {completed_chunks}/{total_chunks}, "
        f"fallback chunks: {fallback_chunks}",
        flush=True,
    )
    return "".join(out)


def translate_file(
    src: Path,
    dst: Path,
    translator: GoogleTranslator,
    chunk_size: int,
    chunk_timeout_seconds: int,
    max_retries: int,
    log_every_chunk: bool,
) -> None:
    lines = src.read_text(encoding="utf-8").splitlines(True)
    output: list[str] = []
    buffer: list[str] = []
    in_code = False
    in_math = False

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        output.append(
            translate_text(
                "".join(buffer),
                translator,
                chunk_size,
                chunk_timeout_seconds,
                max_retries,
                str(src.relative_to(ROOT)),
                log_every_chunk,
            )
        )
        buffer = []

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
    dst.write_text("".join(output), encoding="utf-8")


def main() -> None:
    args = parse_args()
    translator = GoogleTranslator(source=args.source_lang, target=args.target_lang)

    start = time.time()
    for index, file in enumerate(args.files, start=1):
        src = ROOT / file
        dst = destination_for(src, args.target_lang)
        print(
            f"[{index}/{len(args.files)}] Translating {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}",
            flush=True,
        )
        file_start = time.time()
        translate_file(
            src,
            dst,
            translator,
            args.chunk_size,
            args.chunk_timeout_seconds,
            args.max_retries,
            args.log_every_chunk,
        )
        print(
            f"[{index}/{len(args.files)}] Finished {src.relative_to(ROOT)} in {time.time() - file_start:.1f}s",
            flush=True,
        )

    print(
        f"Translation complete for target language {args.target_lang} in {time.time() - start:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
