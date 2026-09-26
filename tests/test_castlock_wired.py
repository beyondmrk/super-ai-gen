import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fire as F


class CastLockWired(unittest.TestCase):
    """fire.py's motion preflight runs castlock: a character on camera twice with no master + voice
    is an error before any credit is spent."""

    def test_unlocked_recurring_character_blocks_motion(self):
        m = {"project": {"client": "acme", "root": ".", "prefix": "X", "models": {"still": "nano_banana_flash",
                         "motion": "kling3_0", "motion_mode": "std"}, "locks": {"voice_lock": "v"},
                         "entities": [{"id": "CHAR_A", "kind": "character"}], "on_camera_kinds": ["TH"]},
             "stills": [{"tag": "S01", "prompt": "p"}, {"tag": "S02", "prompt": "q"}],
             "shots": [{"tag": "L01", "kind": "TH", "seed": "S01", "duration": 5, "entities": ["CHAR_A"],
                        "dialogue": "hello there", "action": "she leans in, camera pushes in"},
                       {"tag": "L02", "kind": "TH", "seed": "S02", "duration": 5, "entities": ["CHAR_A"],
                        "dialogue": "and more", "action": "she smiles, camera drifts"}]}
        client = {"engine": "cli", "models_allowed": ["nano_banana_flash", "kling3_0"]}
        errs, _ = F.preflight(m, client, "motion", m["stills"], m["shots"], True)
        self.assertTrue(any("cast lock" in e and "CHAR_A" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
