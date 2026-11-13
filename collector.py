import json
import sqlite3


class Collector:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS inbox(
          capture TEXT, seq INTEGER, body TEXT,
          PRIMARY KEY(capture,seq));
        CREATE TABLE IF NOT EXISTS slot(
          wire TEXT PRIMARY KEY, capture TEXT NOT NULL, tree TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pending(
          id TEXT PRIMARY KEY, wire TEXT NOT NULL, path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stage(
          wire TEXT, station_pos INTEGER, reading_pos INTEGER,
          station TEXT, rid TEXT, value INTEGER, quality TEXT,
          PRIMARY KEY(wire,station_pos,reading_pos));
        CREATE TABLE IF NOT EXISTS published(
          capture TEXT PRIMARY KEY, rows TEXT NOT NULL);
        """)

    def accept(self, frame):
        body = json.dumps(frame, sort_keys=True, separators=(",", ":"))
        capture, seq, wire = frame["capture"], frame["seq"], frame["wire"]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                "SELECT body FROM inbox WHERE capture=? AND seq=?",
                (capture, seq)).fetchone()
            if old:
                if old[0] != body:
                    raise ValueError("FRAME_CONFLICT")
                self.db.execute("COMMIT")
                return
            self.db.execute("INSERT INTO inbox VALUES(?,?,?)", (capture, seq, body))
            self._apply(frame, wire, capture)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _apply(self, frame, wire, capture):
        if "data" in frame:
            tree = frame["data"]
            self.db.execute("INSERT OR REPLACE INTO slot VALUES(?,?,?)",
                            (wire, capture, json.dumps(tree)))
            for i, station in enumerate(tree["stations"]):
                if station is not None:
                    self._stage(wire, i, 0, station["readings"], station.get("code"))
        else:
            tree = json.loads(self.db.execute(
                "SELECT tree FROM slot WHERE wire=?", (wire,)).fetchone()[0])
        for entry in frame.get("pending", []):
            self.db.execute("INSERT OR REPLACE INTO pending VALUES(?,?,?)",
                            (entry["id"], wire, json.dumps(entry["path"])))
        for patch in frame.get("patches", []):
            target = self.db.execute(
                "SELECT path FROM pending WHERE id=?", (patch["id"],)).fetchone()
            path = json.loads(target[0])
            if path == ["stations"]:
                start = patch["at"]
                while len(tree["stations"]) < start + len(patch["items"]):
                    tree["stations"].append(None)
                for offset, item in enumerate(patch["items"]):
                    i = start + offset
                    tree["stations"][i] = item
                    if item is not None:
                        self._stage(wire, i, 0, item["readings"], item.get("code"))
                continue
            i = path[1]
            station = tree["stations"][i]
            if patch["kind"] == "items":
                self._stage(wire, i, patch["at"], patch["items"],
                            None if station is None else station.get("code"))
            elif patch["kind"] == "merge" and station is not None:
                station.update(patch["data"])
            elif patch["kind"] == "error":
                tree["stations"][i] = None
        for token in frame.get("completed", []):
            self.db.execute("DELETE FROM pending WHERE id=?", (token,))
        self.db.execute("UPDATE slot SET tree=? WHERE wire=?",
                        (json.dumps(tree), wire))
        if not frame["hasNext"]:
            rows = self.db.execute(
                "SELECT station,rid,value,quality FROM stage WHERE wire=? "
                "ORDER BY station_pos,reading_pos", (wire,)).fetchall()
            self.db.execute("INSERT OR REPLACE INTO published VALUES(?,?)",
                            (capture, json.dumps(rows)))

    def _stage(self, wire, station_pos, at, items, code):
        for offset, item in enumerate(items):
            self.db.execute("INSERT OR REPLACE INTO stage VALUES(?,?,?,?,?,?,?)",
                            (wire, station_pos, at + offset, code,
                             item["rid"], item["value"], item.get("quality")))

    def rows(self, capture):
        row = self.db.execute("SELECT rows FROM published WHERE capture=?",
                              (capture,)).fetchone()
        return None if row is None else json.loads(row[0])

    def state(self):
        return {
            "inbox": self.db.execute(
                "SELECT capture,seq FROM inbox ORDER BY capture,seq").fetchall(),
            "slots": self.db.execute(
                "SELECT wire,capture,tree FROM slot ORDER BY wire").fetchall(),
            "pending": self.db.execute(
                "SELECT id,wire,path FROM pending ORDER BY id").fetchall(),
            "staged": self.db.execute(
                "SELECT * FROM stage ORDER BY wire,station_pos,reading_pos").fetchall(),
            "published": self.db.execute(
                "SELECT capture,rows FROM published ORDER BY capture").fetchall(),
        }
