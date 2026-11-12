"""Checks the mood estimate and pitch detection. Run: python -m unittest test_mood_core"""

import unittest

import numpy as np

import mood_core as core


def sine(hz, rate, count):
    return 0.5 * np.sin(2 * np.pi * hz * np.arange(count) / rate)


class MoodTests(unittest.TestCase):
    none = core.empty_features()

    def test_words(self):
        self.assertEqual(core.words("I can’t sleep, OK?"), ["i", "can't", "sleep", "ok"])

    def test_keyword_moods(self):
        cases = {
            "I am so happy and proud of the crew today": "happy",
            "I feel tired and worn out and I need rest": "tired",
            "I feel hopeless and worthless and empty": "lowMood",
            "I am furious and fed up": "angry",
        }
        for text, expected in cases.items():
            self.assertEqual(core.analyze(text, self.none, None)["mood"], expected, text)

    def test_negation(self):
        reading = core.analyze("I am not happy and I miss home", self.none, None)
        self.assertEqual(reading["mood"], "sad")
        self.assertFalse(any(r.startswith("Happy words") for r in reading["reasons"]))

    def test_empty_input(self):
        reading = core.analyze("", self.none, None)
        self.assertEqual(reading["mood"], "calm")
        self.assertEqual(reading["confidence"], "Low")
        self.assertIn("Very few words were heard", reading["reasons"])
        self.assertAlmostEqual(sum(s["share"] for s in reading["scores"]), 1)

    def test_features(self):
        frames = [{"db": -60, "pitch": None, "seconds": 0.1}] * 4 + [{"db": -20, "pitch": 150, "seconds": 0.1}] * 16
        features = core.make_features(frames, 6)
        self.assertAlmostEqual(features["speechSeconds"], 1.6)
        self.assertAlmostEqual(features["pitchHz"], 150)
        self.assertAlmostEqual(features["pauseRatio"], 0.2)
        self.assertAlmostEqual(features["wordsPerMinute"], 225)

    def test_baseline_comparison(self):
        base = {"energyDB": -30, "pitchHz": 150, "pitchVariation": 0.1, "wordsPerMinute": 150,
                "pauseRatio": 0.2, "speechSeconds": 10}
        loud = dict(base, energyDB=-20, wordsPerMinute=200, speechSeconds=5)
        reading = core.analyze("this is unacceptable and I hate it", loud, base)
        self.assertEqual(reading["mood"], "angry")
        self.assertEqual(reading["confidence"], "High")
        self.assertIn("Voice 10 dB louder than baseline", reading["reasons"])
        self.assertIn("Speaking 33% faster than baseline", reading["reasons"])

    def test_pitch(self):
        for rate in (16000, 44100, 48000):
            for hz in (110, 150, 220, 330):
                found = core.detect_pitch(sine(hz, rate, 4096), rate)
                self.assertIsNotNone(found)
                self.assertLess(abs(found - hz) / hz, 0.03, f"{hz} Hz at {rate}")
        self.assertIsNone(core.detect_pitch(np.zeros(4096), 16000))


if __name__ == "__main__":
    unittest.main()
