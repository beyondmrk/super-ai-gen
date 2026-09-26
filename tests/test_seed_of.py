import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fire as F


class SeedOf(unittest.TestCase):
    def test_basename_of_start_image(self):
        cmd = ["hf", "generate", "create", "--model", "kling3_0", "--prompt", "x",
               "--start-image", "D:/p/Elements/Stills/PETLAB_S01_v04.png"]
        self.assertEqual(F.seed_of(cmd), "PETLAB_S01_v04.png")

    def test_none_without_a_start_image(self):
        self.assertIsNone(F.seed_of(["hf", "generate", "create", "--prompt", "x"]))


if __name__ == "__main__":
    unittest.main()
