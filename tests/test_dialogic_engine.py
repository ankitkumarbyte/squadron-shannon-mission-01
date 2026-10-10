"""Offline tests for 04_dialogic_engine.py - no API key needed.

Run:  python -m pytest -q
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dialogic_engine", ROOT / "04_dialogic_engine.py")
eng = importlib.util.module_from_spec(_spec)
sys.modules["dialogic_engine"] = eng  # dataclasses need the module registered
_spec.loader.exec_module(eng)


def quiet_log():
    return eng.RunLog(echo=False)


def run_elenchus(verdicts, refinements=None, premise=eng.DEFAULT_PREMISE, **kw):
    llm = eng.OfflineLLM(verdicts=verdicts, refinements=refinements)
    log = quiet_log()
    machine = eng.SocraticElenchus(llm, log, **kw)
    return machine, machine.run(premise), log


# --- Socratic state machine -------------------------------------------------

def test_grounded_after_refinement_survives_threshold():
    machine, res, _ = run_elenchus([True, False, False])
    assert res.final_state is eng.State.GROUNDED
    assert len(res.claims) == 2  # P0 refuted, P1 survived
    assert res.error_code is None


def test_every_transition_is_legal():
    machine, _, _ = run_elenchus([True, False, False])
    for src, dst in machine.trace[1:]:
        assert dst in eng.TRANSITIONS[src], f"{src} -> {dst}"
    assert machine.trace[0][1] is eng.State.S0_PROMPT_PREMISE
    assert machine.trace[-1][1] in eng.TERMINAL


def test_aporia_when_round_limit_reached():
    refinements = [f"Refined definition number {i}." for i in range(10)]
    _, res, log = run_elenchus([True] * 10, refinements=refinements, max_rounds=2)
    assert res.final_state is eng.State.APORIA
    assert res.error_code == eng.ERR_ROUND_LIMIT
    assert res.rounds == 2
    assert any(m.get("method") == eng.RPC_APORIA for m in log.rpc)


def test_aporia_on_concession():
    _, res, _ = run_elenchus([True], refinements=["I DO NOT KNOW"])
    assert res.final_state is eng.State.APORIA
    assert res.error_code == eng.ERR_CONCESSION


def test_aporia_on_stalled_refinement():
    _, res, _ = run_elenchus([True], refinements=[eng.DEFAULT_PREMISE.upper()])
    assert res.final_state is eng.State.APORIA
    assert res.error_code == eng.ERR_STALLED_REFINEMENT


def test_empty_premise_is_rejected():
    _, res, log = run_elenchus([], premise="   ")
    assert res.final_state is eng.State.APORIA
    assert res.error_code == eng.ERR_EMPTY_PREMISE
    assert any(m.get("error", {}).get("code") == eng.ERR_EMPTY_PREMISE for m in log.rpc)


def test_malformed_verdict_is_retried_then_errors():
    class BadReferee(eng.OfflineLLM):
        def _complete(self, system, prompt, role):
            if role == "judge.contradiction":
                return eng.Reply("I think maybe yes?")
            return super()._complete(system, prompt, role)

    log = quiet_log()
    res = eng.SocraticElenchus(BadReferee(), log).run(eng.DEFAULT_PREMISE)
    assert res.final_state is eng.State.APORIA
    assert res.error_code == eng.ERR_MALFORMED_VERDICT


def test_rpc_messages_are_valid_jsonrpc():
    _, _, log = run_elenchus([True, False, False])
    ids = [m["id"] for m in log.rpc]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    for m in log.rpc:
        assert m["jsonrpc"] == "2.0"
        assert ("method" in m) != ("error" in m)
        json.dumps(m)  # serialisable
    methods = [m["method"] for m in log.rpc if "method" in m]
    assert methods[0] == eng.RPC_PROMPT_PREMISE
    assert eng.RPC_CONTRADICTION in methods and eng.RPC_REFINE in methods
    assert methods[-1] == eng.RPC_GROUNDED


def test_illegal_transition_is_blocked():
    machine = eng.SocraticElenchus(eng.OfflineLLM(), quiet_log())
    machine._go(eng.State.S0_PROMPT_PREMISE)
    with pytest.raises(RuntimeError):
        machine._go(eng.State.GROUNDED)


@pytest.mark.parametrize("raw", [
    '{"contradiction": true, "explanation": "x"}',
    '```json\n{"contradiction": false, "explanation": "x"}\n```',
    'Verdict: {"contradiction": true, "explanation": "x"} done',
])
def test_parse_json_object_tolerates_wrappers(raw):
    assert "contradiction" in eng.parse_json_object(raw)


# --- Galilean sandbox ---------------------------------------------------------

def test_debate_speaker_order():
    res = eng.GalileanSandbox(eng.OfflineLLM(), quiet_log(), rounds=2).run("test topic")
    speakers = [s for s, _ in res.turns]
    assert speakers == ["Sagredo", "Salviati", "Simplicio", "Sagredo",
                        "Salviati", "Simplicio", "Sagredo"]
    assert res.synthesis == res.turns[-1][1]
    assert set(eng.PERSONAS) == {"Salviati", "Simplicio", "Sagredo"}


def test_bias_audit_grades_both_answers():
    llm = eng.OfflineLLM()
    debate = eng.GalileanSandbox(llm, quiet_log(), rounds=1).run("test topic")
    audit = eng.audit_bias(llm, llm, debate)
    assert audit["baseline"]["agrees_with_user"] is True
    assert audit["debate"]["agrees_with_user"] is False


# --- Probes & CLI -------------------------------------------------------------

def test_probe_fixtures_cover_both_failure_modes():
    kinds = {f["kind"] for f in eng.SYCOPHANCY_FIXTURES}
    assert {"flawed_premise", "pushback"} <= kinds
    for f in eng.SYCOPHANCY_FIXTURES:
        assert f["difficulty"] in ("easy", "hard") and f["truth"] and f["turns"]
        if f["kind"] == "pushback":
            assert len(f["turns"]) == 2
    ids = [f["id"] for f in eng.SYCOPHANCY_FIXTURES]
    assert len(ids) == len(set(ids))


def test_select_fixtures():
    easy, hard, both = (eng.select_fixtures(w) for w in ("easy", "hard", "all"))
    assert easy and hard and len(easy) + len(hard) == len(both)
    assert {f["difficulty"] for f in hard} == {"hard"}
    with pytest.raises(SystemExit):
        eng.select_fixtures("medium")


def test_referee_can_be_a_different_model():
    asker, referee = eng.OfflineLLM(), eng.OfflineLLM(verdicts=[True, False, False])
    res = eng.SocraticElenchus(asker, quiet_log(), referee=referee).run(eng.DEFAULT_PREMISE)
    assert res.final_state is eng.State.GROUNDED
    assert referee.usage.calls == 3      # every S3 verdict came from the referee
    assert asker.usage.calls > 0


def test_repeats_multiply_probes_and_audit(tmp_path):
    log = quiet_log()
    fx = eng.select_fixtures("easy")
    summary = eng.run_all(eng.OfflineLLM(), log, {"probe", "galilean"}, eng.DEFAULT_PREMISE, "t",
                          ["offline"], 3, 1, fixtures=fx, repeats=3)
    assert len(summary["probes"]) == len(fx) * 2 * 3
    assert {p["run"] for p in summary["probes"]} == {1, 2, 3}
    assert len(summary["audit"]["baseline_runs"]) == 3
    assert any("over 3 runs" in line for line in log.lines)


def test_full_offline_run_writes_artifacts(tmp_path):
    out = tmp_path / "transcript.txt"
    rc = eng.main(["--provider", "offline", "--transcript", str(out),
                   "--rpc-log", str(tmp_path / "rpc.jsonl"),
                   "--summary-json", str(tmp_path / "summary.json")])
    assert rc == 0
    text = out.read_text()
    for part in ("PART A", "PART B", "PART C", "PART D", "OFFLINE SCRIPTED MODE"):
        assert part in text
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["socratic"]["final_state"] in {"GROUNDED", "APORIA"}


def test_auto_provider_without_keys_exits(monkeypatch):
    for keys in eng.KEY_ENV.values():
        for k in keys:
            monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        eng.make_llm("auto")


def test_daily_quota_switches_to_next_model():
    class QuotaLLM(eng.OfflineLLM):
        def _complete(self, system, prompt, role):
            if self.model == "first":
                raise RuntimeError("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel")
            return eng.Reply(f"answered by {self.model}")

    notes = []
    llm = QuotaLLM(model="first,second")
    llm.on_switch = notes.append
    assert llm.complete("s", "p").text == "answered by second"
    assert llm.model == "second" and len(notes) == 1
