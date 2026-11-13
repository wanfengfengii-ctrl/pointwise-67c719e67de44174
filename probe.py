import json
import sys
from collector import Collector
from samples import EXPECTED, cases


def observe(name, stop=None, reopen=False):
    source = cases()[name]
    frames = source if stop is None else source[:stop]
    worker = Collector()
    errors = []
    for number, frame in enumerate(frames, 1):
        try:
            worker.accept(frame)
        except Exception as exc:
            errors.append([number, type(exc).__name__, str(exc)])
        if reopen:
            replacement = Collector()
            worker.db.backup(replacement.db)
            worker.db.close()
            worker = replacement
    captures = sorted({frame["capture"] for frame in source})
    result = {
        "case": name, "accepted_prefix": len(frames), "errors": errors,
        "actual": {capture: worker.rows(capture) for capture in captures},
        "complete_expected": EXPECTED[name], "state": worker.state(),
    }
    worker.db.close()
    return json.loads(json.dumps(result))


if __name__ == "__main__":
    name = sys.argv[1]
    stop = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(observe(name, stop), ensure_ascii=False, sort_keys=True))
