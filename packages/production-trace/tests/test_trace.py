import json

from production_trace import (
    ManualGuide,
    TraceRecorder,
    read_history,
    seconds_to_timecode,
    verify_trace,
)
from production_trace.cli import main


def make_job(tmp_path, body=b"hello"):
    job_dir = tmp_path / "jobs" / "j1"
    job_dir.mkdir(parents=True)
    (job_dir / "out.txt").write_bytes(body)
    recorder = TraceRecorder(tool_id="example-v1a", job_id="j1", engine_version="e1", recipe_version="r1")
    recorder.input_json("request", {"secret_brief": "PRIVATE-TEXT"})
    with recorder.step("render", "write_text", chars=5, long_text="x" * 1000):
        pass
    recorder.artifact(job_dir / "out.txt", media_type="text/plain")
    guide = ManualGuide(program="Notepad", alternatives=["TextEdit"], estimated_minutes=1)
    guide.layer("Body", "text", content="hello")
    guide.step("พิมพ์ข้อความ", tool="Edit ▸ Paste", target="บรรทัดที่ 1", value="hello")
    guide.checks.append("ไฟล์มี 5 ตัวอักษร")
    return recorder.finish(status="accepted", job_dir=job_dir, output_root=tmp_path, guide=guide)


def test_trace_history_and_manual(tmp_path):
    trace = make_job(tmp_path)
    assert trace["schema_version"] == "production-trace.v1"
    assert trace["steps"][0]["params"]["long_text"]["chars"] == 1000
    serialized = (tmp_path / "jobs" / "j1" / "trace.json").read_text(encoding="utf-8")
    assert "PRIVATE-TEXT" not in serialized
    assert read_history(tmp_path)[0]["job_id"] == "j1"
    manual = (tmp_path / "jobs" / "j1" / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "วิธีทำเองด้วย Notepad" in manual and "`Edit ▸ Paste`" in manual and "| 1 | Body |" in manual
    assert "PRIVATE-TEXT" not in (tmp_path / "history.jsonl").read_text(encoding="utf-8")


def test_verify_detects_tampering(tmp_path):
    make_job(tmp_path)
    assert verify_trace(tmp_path, "j1")["ok"] is True
    (tmp_path / "jobs" / "j1" / "out.txt").write_bytes(b"changed")
    assert verify_trace(tmp_path, "j1")["ok"] is False


def test_cli_history_show_manual_verify(tmp_path, capsys):
    make_job(tmp_path)
    assert main(["history", "--root", str(tmp_path)]) == 0
    assert "example-v1a" in capsys.readouterr().out
    assert main(["show", "--root", str(tmp_path), "j1"]) == 0
    assert "write_text" in capsys.readouterr().out
    assert main(["manual", "--root", str(tmp_path), "j1"]) == 0
    assert "Notepad" in capsys.readouterr().out
    assert main(["verify", "--root", str(tmp_path), "j1"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["show", "--root", str(tmp_path), "missing"]) == 2


def test_timecode():
    assert seconds_to_timecode(83.4) == "00:01:23.400"
    from production_trace import frames_to_timecode

    assert frames_to_timecode(1.5, 30) == "00:00:01:15"
