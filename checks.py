import json
from pathlib import Path
from probe import observe
from samples import EXPECTED


def verify():
    normal = observe("normal")
    assert normal["errors"] == []
    assert normal["actual"] == EXPECTED["normal"]
    assert observe("normal", 2)["actual"] == {"A": None}
    assert observe("normal", reopen=True) == normal
    from collector import Collector
    from samples import cases
    worker = Collector()
    for frame in cases()["normal"]:
        worker.accept(frame)
        worker.accept(frame)
    assert worker.rows("A") == EXPECTED["normal"]["A"]
    before = worker.state()
    conflict = dict(cases()["normal"][0], hasNext=False)
    try:
        worker.accept(conflict)
    except ValueError as exc:
        assert str(exc) == "FRAME_CONFLICT"
    else:
        raise AssertionError("conflicting replay accepted")
    assert worker.state() == before
    worker.db.close()
    print("BASELINE_BUSINESS_OK")
    if Path("DESIGN.md").exists():
        report = Path("DESIGN.md").read_text(encoding="utf-8")
        assert report.strip(), "empty design"
        evidence = json.loads(Path("evidence.json").read_text(encoding="utf-8"))
        assert evidence, "empty evidence"
        for entry in evidence:
            actual = observe(entry["case"], entry["stop"], entry["reopen"])
            assert actual == entry["observed"], entry["case"]
        print("REPLAYED_DESIGN_OBSERVATIONS", len(evidence))


if __name__ == "__main__":
    verify()
