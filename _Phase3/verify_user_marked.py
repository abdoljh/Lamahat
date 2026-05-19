#!/usr/bin/env python3
"""
Verify the user-marked-file resolution patch.

Run this AFTER replacing phase3/sources/decisions.py with the patched
version.  No network, no Anthropic key, no FFmpeg — pure logic check
against a temporary review/ tree.

Usage (from _Phase3/):
    python verify_user_marked.py

Exits 0 on success, 1 on any mismatch.
"""
import logging
import shutil
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("verify")

# Import from the repo
sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase3.sources.decisions import (
    Decisions, ShotDecision, find_user_marked_file, _USER_FILE_RE,
)


def regex_tests():
    cases = [
        ("my_jafar.jpg",        True),
        ("MY_JAFAR.JPG",        True),
        ("My_Jafar.Jpg",        True),
        ("user_a.png",          True),
        ("USER_PORTRAIT.WEBP",  True),
        ("my-jafar.jpeg",       True),
        ("pexels_a.jpg",        False),
        ("loc_a.jpg",           False),
        ("wikimedia_b.jpg",     False),
        ("character.jpg",       False),
        ("my.jpg",              False),
        ("myfile.jpg",          False),
        ("my_jafar.tiff",       False),
    ]
    fails = 0
    for name, expected in cases:
        got = bool(_USER_FILE_RE.match(name))
        if got != expected:
            log.error("regex %r → %s, expected %s", name, got, expected)
            fails += 1
    log.info("regex: %d cases, %d failed", len(cases), fails)
    return fails == 0


def resolution_tests():
    """Build a fake review dir, run resolve() across 4 scenarios."""
    work = Path(tempfile.mkdtemp(prefix="verify_review_"))
    review = work / "review"
    try:
        review.mkdir()
        (review / "overrides").mkdir()
        (review / "overrides" / "character.jpg").write_bytes(b"pin")

        # Scenario A — portrait shot with both pin and user file → user wins
        (review / "shot_05_portrait").mkdir()
        (review / "shot_05_portrait" / "pexels_a.jpg").write_bytes(b"auto")
        (review / "shot_05_portrait" / "my_jafar.jpg").write_bytes(b"user")

        # Scenario B — portrait shot, pin only
        (review / "shot_16_portrait").mkdir()
        (review / "shot_16_portrait" / "pexels_a.jpg").write_bytes(b"auto")

        # Scenario C — broll shot, chosen_file only
        (review / "shot_28_broll").mkdir()
        (review / "shot_28_broll" / "pexels_b.jpg").write_bytes(b"auto")

        # Scenario D — object shot with empty chosen_file but user file
        (review / "shot_44_object").mkdir()
        (review / "shot_44_object" / "User_Map.PNG").write_bytes(b"user-map")

        decisions = Decisions(
            book={"title": "test", "character": "Jafar al-Askari"},
            pinned_portrait="overrides/character.jpg",
            shots={
                5:  ShotDecision(visual="portrait", query="q",
                                 duration_sec=7,
                                 chosen_file="shot_05_portrait/pexels_a.jpg"),
                16: ShotDecision(visual="portrait", query="q",
                                 duration_sec=7,
                                 chosen_file="shot_16_portrait/pexels_a.jpg"),
                28: ShotDecision(visual="broll", query="q",
                                 duration_sec=6,
                                 chosen_file="shot_28_broll/pexels_b.jpg"),
                44: ShotDecision(visual="object", query="q",
                                 duration_sec=8,
                                 chosen_file=""),
            },
        )

        expected = {
            5:  "my_jafar.jpg",     # user wins over pin
            16: "character.jpg",    # pin applies (no user file)
            28: "pexels_b.jpg",     # chosen_file applies
            44: "User_Map.PNG",     # user file rescues empty chosen_file
        }

        fails = 0
        for idx, want in expected.items():
            got = decisions.resolve(idx, review)
            got_name = got.name if got else None
            if got_name != want:
                log.error("shot %d resolved to %s, expected %s",
                          idx, got_name, want)
                fails += 1
        log.info("resolution: %d scenarios, %d failed", len(expected), fails)
        return fails == 0
    finally:
        shutil.rmtree(work)


def main():
    ok = True
    log.info("── regex tests ──")
    ok &= regex_tests()
    log.info("── resolution tests ──")
    ok &= resolution_tests()
    if ok:
        log.info("✓ All checks passed")
        return 0
    log.error("✗ Some checks failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
