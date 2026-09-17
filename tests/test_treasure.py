import unittest

from agentarena.core.treasure import (
    DEFAULT_ALPHABET,
    DEFAULT_LENGTH,
    digests_equal,
    generate_treasure,
    treasure_digest,
    validate_treasure,
)


class TestGenerate(unittest.TestCase):
    def test_default_length_and_charset(self):
        t = generate_treasure()
        self.assertEqual(len(t), DEFAULT_LENGTH)
        self.assertTrue(all(c in DEFAULT_ALPHABET for c in t))

    def test_custom_length_and_alphabet(self):
        t = generate_treasure(length=16, alphabet="AB")
        self.assertEqual(len(t), 16)
        self.assertTrue(set(t) <= {"A", "B"})

    def test_uniqueness(self):
        self.assertNotEqual(generate_treasure(), generate_treasure())

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            generate_treasure(length=0)
        with self.assertRaises(ValueError):
            generate_treasure(alphabet="a")


class TestValidate(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(validate_treasure("a" * DEFAULT_LENGTH))

    def test_wrong_length(self):
        self.assertFalse(validate_treasure("a" * (DEFAULT_LENGTH - 1)))

    def test_wrong_charset(self):
        self.assertFalse(validate_treasure("z" * DEFAULT_LENGTH))  # 'z' not in hex

    def test_non_string(self):
        self.assertFalse(validate_treasure(123))  # type: ignore[arg-type]


class TestDigest(unittest.TestCase):
    def test_digest_stable(self):
        t = "ab" * 64
        self.assertEqual(treasure_digest(t), treasure_digest(t))

    def test_digests_equal(self):
        t = generate_treasure()
        self.assertTrue(digests_equal(t, treasure_digest(t)))
        self.assertFalse(digests_equal(generate_treasure(), treasure_digest(t)))


if __name__ == "__main__":
    unittest.main()
