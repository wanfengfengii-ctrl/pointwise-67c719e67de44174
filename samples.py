def frame(capture, seq, wire, has_next=True, **fields):
    return dict(capture=capture, seq=seq, wire=wire, hasNext=has_next, **fields)


def reading(rid, value, quality="ok"):
    return dict(rid=rid, value=value, quality=quality)


def initial(capture="A", wire="7", code="north"):
    return frame(capture, 1, wire,
                 data={"stations": [{"code": code, "readings": []}]},
                 pending=[{"id": "r", "path": ["stations", 0, "readings"]}])


def stream(capture="A", seq=2, wire="7", value=10):
    return frame(capture, seq, wire, patches=[
        {"id": "r", "kind": "items", "at": 0,
         "items": [reading("same", value), reading("same", 0)]}])


def finish(capture="A", seq=3, wire="7"):
    return frame(capture, seq, wire, False, completed=["r"])


def cases():
    normal = [initial(), stream(), finish()]
    delayed = [
        frame("A", 1, "7", data={"stations": [{"readings": []}]},
              pending=[{"id": "r", "path": ["stations", 0, "readings"]},
                       {"id": "m", "path": ["stations", 0]}]),
        stream(),
        frame("A", 3, "7", patches=[
            {"id": "m", "kind": "merge", "data": {"code": "north"}}],
            completed=["m"]),
        finish(seq=4),
    ]
    late_null = [
        initial(), stream(),
        frame("A", 3, "7", False, patches=[
            {"id": "r", "kind": "error",
             "path": ["stations", 0, "readings", 2, "value"],
             "field": "value", "message": "sensor unreadable"}], completed=["r"]),
    ]
    interleaved = [
        initial(),
        frame("B", 1, "8", data={"stations": [
            {"code": "west", "readings": []},
            {"code": "east", "readings": []}]},
            pending=[{"id": "r", "path": ["stations", 1, "readings"]}]),
        stream(), finish(),
        stream(capture="B", wire="8", value=20),
        finish(capture="B", wire="8"),
    ]
    reused = normal + [
        frame("B", 1, "7", data={"stations": [{"code": "south", "readings": []}]}),
        frame("B", 2, "7", False),
    ]
    gap = [initial(), finish()]
    progress = [
        frame("A", 1, "7", data={"stations": []},
              pending=[{"id": "s", "path": ["stations"]}]),
        frame("A", 2, "7", patches=[
            {"id": "s", "kind": "items", "at": 0, "items": [
                {"code": "north", "readings": []}]}],
            pending=[{"id": "r", "path": ["stations", 0, "readings"]}]),
        stream(seq=3),
        frame("A", 4, "7", completed=["r"]),
        frame("A", 5, "7", patches=[
            {"id": "s", "kind": "items", "at": 1, "items": [
                {"code": "south", "readings": []}]}]),
        frame("A", 6, "7", False, completed=["s"]),
    ]
    handoff = progress[:2] + [
        stream(seq=3),
        frame("A", 4, "7", False, completed=["r"]),
    ]
    handoff[1] = dict(handoff[1], completed=["s"])
    holes = [
        initial(),
        frame("A", 2, "7", patches=[
            {"id": "r", "kind": "items", "at": 2, "items": [reading("c", 30)]}]),
        frame("A", 3, "7", patches=[
            {"id": "r", "kind": "items", "at": 0,
             "items": [reading("a", 10), reading("b", 20)]}]),
        finish(seq=4),
    ]
    cancelled = late_null[:2] + [
        dict(late_null[2], hasNext=True, completed=[]),
        frame("A", 4, "7", patches=[
            {"id": "r", "kind": "items", "at": 3, "items": [reading("late", 99)]}]),
        finish(seq=5),
    ]
    return dict(normal=normal, delayed=delayed, late_null=late_null,
                interleaved=interleaved, reused=reused, gap=gap, progress=progress,
                handoff=handoff, holes=holes, cancelled=cancelled)


EXPECTED = {
    "normal": {"A": [["north", "same", 10, "ok"], ["north", "same", 0, "ok"]]},
    "delayed": {"A": [["north", "same", 10, "ok"], ["north", "same", 0, "ok"]]},
    "late_null": {"A": []},
    "interleaved": {
        "A": [["north", "same", 10, "ok"], ["north", "same", 0, "ok"]],
        "B": [["east", "same", 20, "ok"], ["east", "same", 0, "ok"]]},
    "reused": {
        "A": [["north", "same", 10, "ok"], ["north", "same", 0, "ok"]], "B": []},
    "gap": {"A": None},
    "progress": {"A": [["north", "same", 10, "ok"], ["north", "same", 0, "ok"]]},
    "handoff": {"A": [["north", "same", 10, "ok"], ["north", "same", 0, "ok"]]},
    "holes": {"A": [["north", "a", 10, "ok"], ["north", "b", 20, "ok"],
                    ["north", "c", 30, "ok"]]},
    "cancelled": {"A": []},
}
