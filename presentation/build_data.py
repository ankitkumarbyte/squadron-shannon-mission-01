#!/usr/bin/env python3
"""Build presentation/data.js from the real run logs (no hand-typed results).

Sources:
  05_Execution_Transcript.txt   Parts A, C, D (probes, debate, bias audit)
  logs/rpc_trace.jsonl          Part B (Socratic elenchus, full JSON-RPC messages)

Also writes Mission01_Presentation_standalone.html: the same page with the data inlined,
so it works as one file (USB stick, e-mail, Moodle upload).

Run:  python presentation/build_data.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "05_Execution_Transcript.txt"
RPC = ROOT / "logs" / "rpc_trace.jsonl"
OUT = Path(__file__).resolve().parent / "data.js"
STANDALONE = Path(__file__).resolve().parent / "Mission01_Presentation_standalone.html"

SPEAKER = re.compile(r"^(\S[^|]*?)\s*\| ?(.*)$")
CONT = re.compile(r"^ {14}\| ?(.*)$")


def section(text: str, start: str, end: str | None) -> str:
    a = text.index(start)
    b = text.index(end, a) if end else len(text)
    return text[a:b]


def blocks(lines: list[str]) -> list[tuple[str, str]]:
    """Re-join the transcript's wrapped 'Name | text' blocks into (name, full text)."""
    out: list[tuple[str, list[str]]] = []
    for line in lines:
        c = CONT.match(line)
        if c and out:
            out[-1][1].append(c.group(1))
            continue
        m = SPEAKER.match(line.strip()) if "|" in line else None
        if m and not line.startswith("[") and not line.startswith("="):
            out.append((m.group(1).strip(), [m.group(2)]))
    return [(name, _unwrap(parts)) for name, parts in out]


def _unwrap(parts: list[str]) -> str:
    paras, cur = [], []
    for p in parts:
        if p.strip() == "":
            if cur:
                paras.append(" ".join(cur))
                cur = []
        else:
            cur.append(p.strip())
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras)


def parse_probes(text: str) -> list[dict]:
    part = section(text, "PART A", "Summary (HELD_GROUND")
    items, cur = [], None
    for line in part.splitlines():
        m = re.match(r"^\[(\w+)\s*\] (.+?)\s{2,}(plain|socratic)\s+(\S+)(?: run (\d+)/\d+)?$", line)
        if m:
            cur = {"verdict": m.group(1), "model": m.group(2).strip(), "system": m.group(3),
                   "fixture": m.group(4), "run": int(m.group(5) or 1), "lines": []}
            items.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for it in items:
        bl = dict(blocks(it.pop("lines")))
        it["answer"] = bl.get("answer", "")
        it["judge"] = bl.get("judge", "")
    return items


def parse_debate(text: str) -> dict:
    part = section(text, "PART C", "PART D")
    turns, rnd = [], 0
    pending: list[str] = []
    for line in part.splitlines():
        r = re.match(r"^\s+-- round (\d+) of (\d+)", line)
        if r:
            turns += [dict(t, round=rnd) for t in _named(pending)]
            pending, rnd = [], int(r.group(1))
            continue
        pending.append(line)
    turns += [dict(t, round=rnd) for t in _named(pending)]
    topic = next(t["text"] for t in turns if t["speaker"] == "Topic")
    usage = re.search(r"Usage\s+: (\d+) LLM calls \| (\d+) input tokens \| (\d+) output tokens",
                      part)
    return {"topic": topic,
            "turns": [t for t in turns if t["speaker"] in ("Sagredo", "Salviati", "Simplicio")],
            "calls": int(usage.group(1)), "tokens_in": int(usage.group(2)),
            "tokens_out": int(usage.group(3))}


def _named(lines: list[str]) -> list[dict]:
    return [{"speaker": n, "text": t} for n, t in blocks(lines)]


def parse_audit(text: str) -> dict:
    part = section(text, "PART D", "RUN COMPLETE")
    bl = blocks(part.splitlines())
    named = {n: t for n, t in bl}
    judges = [t for n, t in bl if n == "judge"]
    rows = re.findall(r"(Single agent|3-agent debate)\s+agrees_with_user=(\w+)\s+counterarguments=(\d+)",
                      part)
    grades = {name: {"agrees": a == "True", "counterarguments": int(c)} for name, a, c in rows}
    for (name, _a, _c), j in zip(rows, judges):
        grades[name]["judge"] = j
    return {"biased_user": named.get("Biased user", ""), "single_answer": named.get("Single agent", ""),
            "single": grades.get("Single agent"), "debate": grades.get("3-agent debate")}


def parse_header(text: str) -> dict:
    runs = re.findall(r"^(Run \d)\s+: (\S+)", text, re.M)
    switch = re.search(r">> MODEL SWITCH: (.+)", text)
    return {"runs": [{"name": n, "started": t} for n, t in runs],
            "model_switch": switch.group(1).strip() if switch else None}


def socratic_from_rpc() -> dict:
    msgs = [json.loads(l) for l in RPC.read_text(encoding="utf-8").splitlines() if l.strip()]
    claims = [m["params"]["claim"] for m in msgs
              if m.get("method") in ("elenchus.PROMPT_PREMISE", "elenchus.REFINE_HYPOTHESIS")]
    final = next(m for m in msgs if m.get("method") in ("elenchus.APORIA_REACHED",
                                                       "elenchus.GROUNDING_REACHED"))
    return {"messages": msgs, "claims": claims,
            "final_state": "APORIA" if final["method"].endswith("APORIA_REACHED") else "GROUNDED",
            "reason": final["params"]["reason"]}


def engine_constants() -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("dialogic_engine", ROOT / "04_dialogic_engine.py")
    eng = importlib.util.module_from_spec(spec)
    sys.modules["dialogic_engine"] = eng
    spec.loader.exec_module(eng)
    return {"fixtures": eng.SYCOPHANCY_FIXTURES, "personas": eng.PERSONAS,
            "socratic_prompt": eng.SOCRATES_SYSTEM, "premise": eng.DEFAULT_PREMISE}


def engineering_stats() -> dict:
    engine = (ROOT / "04_dialogic_engine.py").read_text(encoding="utf-8")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")],
                           capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"(\d+) passed", tests.stdout)
    return {"engine_lines": engine.count("\n") + 1,
            "tests_passed": int(m.group(1)) if m else 0,
            "tests_failed": "failed" in tests.stdout}


def main() -> None:
    text = TRANSCRIPT.read_text(encoding="utf-8")
    data = {
        "header": parse_header(text),
        "probes": parse_probes(text),
        "socratic": socratic_from_rpc(),
        "debate": parse_debate(text),
        "audit": parse_audit(text),
        "engineering": engineering_stats(),
        "engine": engine_constants(),
    }
    OUT.write_text("// Generated by build_data.py from the real run logs. Do not edit by hand.\n"
                   "window.MISSION_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n",
                   encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")
    tag = '<script src="data.js"></script>'
    assert tag in page, "index.html must load data.js"
    STANDALONE.write_text(page.replace(tag, "<script>window.MISSION_DATA = " + js + ";</script>"),
                          encoding="utf-8")
    p, d = data["probes"], data["debate"]
    print(f"probes={len(p)} held={sum(x['verdict'] == 'HELD_GROUND' for x in p)} "
          f"claims={len(data['socratic']['claims'])} final={data['socratic']['final_state']} "
          f"debate_turns={len(d['turns'])} audit={data['audit']['single']} / {data['audit']['debate']} "
          f"tests={data['engineering']['tests_passed']} -> {OUT}")


if __name__ == "__main__":
    main()
