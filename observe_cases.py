"""Regenerate evidence.json by calling only the existing probe entry point.

Each entry records the ORIGINAL collector's observed behavior for one
recorded prefix: python3 observe_cases.py  (then python3 checks.py replays
every entry and compares it against a fresh probe.observe call).
"""
import json
from probe import observe

# (case, stop, reopen) — stop=None means the whole recording.
PLAN = [
    ("normal", None, False),   # happy path: complete output matches EXPECTED
    ("normal", 2, False),      # unfinished prefix: rows() must stay None
    ("normal", None, True),    # per-frame backup+reopen is deterministic
    ("progress", 3, False),    # readings task still open: nothing determinable
    ("progress", 4, False),    # readings task done, stations task open: rows
                               # already determined, original still holds them
    ("progress", 5, False),    # taskless second station created; still held
    ("progress", None, False), # complete export: two north rows only
    ("progress", 4, True),     # reopen at the would-be release boundary
    ("delayed", 2, False),     # code not arrived: staged rows carry NULL code
    ("delayed", 3, False),     # merge landed in tree, staged rows still NULL
    ("delayed", None, False),  # original publishes NULL code vs expected north
    ("late_null", 2, False),   # rows staged while an error may still null them
    ("late_null", None, False),# original publishes rows the error invalidated
    ("gap", None, False),      # end frame first: original publishes [] early
    ("reused", None, False),   # wire reuse: B inherits A's staged rows
    ("interleaved", None, False),  # same pending id across captures collides
    ("cancelled", None, False),# late patch to cancelled task gets published
    ("holes", None, False),    # out-of-order positions fill holes correctly
    ("handoff", None, False),  # task completed in the frame that created it
]


def main():
    evidence = [
        {"case": case, "stop": stop, "reopen": reopen,
         "observed": observe(case, stop, reopen)}
        for case, stop, reopen in PLAN
    ]
    with open("evidence.json", "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, sort_keys=True, indent=1)
        fh.write("\n")
    print("wrote", len(evidence), "observations")


if __name__ == "__main__":
    main()
