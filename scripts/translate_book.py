#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,time,signal
from pathlib import Path
from deep_translator import GoogleTranslator

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'books'/'en-US'
DEFAULT=[ROOT/'index.qmd',SRC/'book.qmd',SRC/'lab.qmd']
PROTECTED=re.compile(r"(`[^`]*`|\$[^$]*\$)")

def args():
 p=argparse.ArgumentParser();p.add_argument('--source-lang',default='en');p.add_argument('--target-lang',default='zh-CN');p.add_argument('--files',nargs='*',default=[str(x.relative_to(ROOT)) for x in DEFAULT]);p.add_argument('--chunk-size',type=int,default=1800);return p.parse_args()

def dst(src:Path,lang:str)->Path:
 if src==ROOT/'index.qmd': return ROOT/'books'/lang/'index.qmd'
 return ROOT/'books'/lang/src.relative_to(SRC)

def chunks(t,n):
 i=0;out=[]
 while i<len(t):
  j=min(len(t),i+n)
  if j<len(t):
   k=t.rfind('\n',i,j)
   if k>i+100:j=k+1
  out.append(t[i:j]);i=j
 return out

class TimeoutExc(Exception):
 pass

def _handler(signum, frame):
 raise TimeoutExc()

def tr_text(t,tr,src,dst_lang,n):
 parts=PROTECTED.split(t);out=[]
 for p in parts:
  if not p: continue
  if (p.startswith('`') and p.endswith('`')) or (p.startswith('$') and p.endswith('$')): out.append(p); continue
  if not p.strip(): out.append(p); continue
  seg=[]
  for c in chunks(p,n):
   ok=False
   for _ in range(3):
    try:
     signal.signal(signal.SIGALRM, _handler)
     signal.alarm(20)
     seg.append(tr.translate(c) or c); ok=True; signal.alarm(0); break
    except Exception:
     signal.alarm(0); time.sleep(1)
   if not ok: seg.append(c)
  out.append(''.join(seg))
 return ''.join(out)

def tr_file(src:Path,dstp:Path,tr,src_lang,dst_lang,n):
 lines=src.read_text(encoding='utf-8').splitlines(True)
 out=[];buf=[];in_code=False;in_math=False
 def flush():
  nonlocal buf
  if buf: out.append(tr_text(''.join(buf),tr,src_lang,dst_lang,n)); buf=[]
 for line in lines:
  s=line.strip()
  if s.startswith('```'): flush(); in_code=not in_code; out.append(line); continue
  if s=='$$': flush(); in_math=not in_math; out.append(line); continue
  if in_code or in_math: out.append(line); continue
  buf.append(line)
  if not s: flush()
 flush(); dstp.parent.mkdir(parents=True,exist_ok=True); dstp.write_text(''.join(out),encoding='utf-8')

def main():
 a=args(); tr=GoogleTranslator(source=a.source_lang,target=a.target_lang)
 for f in a.files:
  s=ROOT/f; d=dst(s,a.target_lang); print(f'Translating {s.relative_to(ROOT)} -> {d.relative_to(ROOT)}', flush=True); tr_file(s,d,tr,a.source_lang,a.target_lang,a.chunk_size)
if __name__=='__main__': main()
