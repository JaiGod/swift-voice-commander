"""Mood estimate and voice measurements, ported from voice.swift.

The result is an estimate for a school prototype.
It is NOT a medical or psychological diagnosis.
"""

import math
import re

import numpy as np

MOODS = {
    "happy": ("Happy", "😀", "#2f8a3e"),
    "calm": ("Calm", "😌", "#2a70b8"),
    "sad": ("Sad", "😢", "#4a51b0"),
    "lowMood": ("Low Mood (possible depression signs)", "😞", "#7d44a8"),
    "anxious": ("Anxious", "😰", "#c96a12"),
    "worried": ("Worried", "😟", "#9a7a06"),
    "angry": ("Angry", "😠", "#c5302b"),
    "tired": ("Tired", "😴", "#66727e"),
}

# Words and short phrases linked to each mood. Add more to improve it.
KEYWORDS = {
    "happy": ["happy", "great", "excited", "wonderful", "amazing", "glad", "joy", "love this",
              "fantastic", "proud", "grateful", "good day"],
    "calm": ["calm", "relaxed", "peaceful", "fine", "steady", "okay", "comfortable"],
    "sad": ["sad", "crying", "cry", "miss home", "miss my family", "lonely", "homesick", "upset",
            "heartbroken", "alone"],
    "lowMood": ["depressed", "hopeless", "empty", "worthless", "numb", "no energy", "pointless",
                "can't go on", "nothing matters", "don't care anymore", "give up"],
    "anxious": ["anxious", "panic", "panicking", "nervous", "scared", "afraid", "terrified",
                "heart racing", "can't breathe", "overwhelmed", "on edge"],
    "worried": ["worried", "worry", "worrying", "concerned", "unsure", "what if", "not sure",
                "problem", "trouble", "uneasy"],
    "angry": ["angry", "furious", "mad", "annoyed", "frustrated", "irritated", "hate", "fed up",
              "unacceptable", "sick of"],
    "tired": ["tired", "exhausted", "sleepy", "fatigued", "drained", "worn out", "can't sleep",
              "no sleep", "need rest"],
}

# Stands in for Apple's NaturalLanguage sentiment score, which Python does not have.
POSITIVE = {"good", "great", "happy", "love", "wonderful", "amazing", "excited", "fantastic", "glad",
            "proud", "grateful", "nice", "beautiful", "best", "enjoy", "enjoyed", "fun", "success",
            "successful", "perfect", "awesome", "better", "hope", "calm", "peaceful", "smooth", "safe",
            "thankful", "joy", "pleased", "delighted"}
NEGATIVE = {"bad", "terrible", "awful", "sad", "hate", "angry", "worst", "horrible", "scared", "afraid",
            "lonely", "depressed", "hopeless", "worried", "anxious", "failure", "failed", "fail",
            "broken", "pain", "hurt", "tired", "exhausted", "upset", "problem", "trouble", "miserable",
            "cry", "crying", "frustrated", "furious", "worthless", "dangerous", "wrong", "sick",
            "annoyed", "nervous", "panic", "empty"}
NEGATORS = {"not", "never", "no", "isn't", "wasn't", "aren't", "don't", "didn't", "won't", "without"}


def rise(value, low, high):
    return min(max((value - low) / (high - low), 0.0), 1.0)


def relative(value, base):
    return value / base - 1 if value > 0 and base > 0 else 0.0


def words(text):
    return re.findall(r"[^\W\d_']+(?:'[^\W\d_']+)*'?|'", text.lower().replace("’", "'"))


def _matches(phrase, spoken):
    """Counts a phrase, skipping cases such as "not happy" or "never really happy"."""
    if not phrase or len(spoken) < len(phrase):
        return 0
    count = 0
    for start in range(len(spoken) - len(phrase) + 1):
        if spoken[start:start + len(phrase)] != phrase:
            continue
        before = spoken[max(0, start - 2):start]
        if not any(word in NEGATORS for word in before):
            count += 1
    return count


def _sentiment(spoken):
    positive = negative = 0
    for i, word in enumerate(spoken):
        negated = any(w in NEGATORS for w in spoken[max(0, i - 2):i])
        if word in POSITIVE:
            if negated:
                negative += 1
            else:
                positive += 1
        elif word in NEGATIVE:
            if negated:
                positive += 1
            else:
                negative += 1
    return (positive - negative) / (positive + negative + 1)


def empty_features():
    return {"energyDB": 0.0, "pitchHz": 0.0, "pitchVariation": 0.0,
            "wordsPerMinute": 0.0, "pauseRatio": 0.0, "speechSeconds": 0.0}


def make_features(frames, word_count):
    """frames: list of dicts with db, pitch (or None) and seconds."""
    if not frames:
        return empty_features()

    # Background noise is estimated from the quietest moments.
    levels = sorted(f["db"] for f in frames)
    noise_floor = levels[len(levels) // 10]
    speech = [f for f in frames if f["db"] > noise_floor + 8]
    if not speech:
        return empty_features()

    total_seconds = sum(f["seconds"] for f in frames)
    speech_seconds = sum(f["seconds"] for f in speech)
    pitches = [f["pitch"] for f in speech if f["pitch"] is not None]

    result = empty_features()
    result["energyDB"] = sum(f["db"] for f in speech) / len(speech)
    result["speechSeconds"] = speech_seconds
    result["pauseRatio"] = 1 - speech_seconds / max(total_seconds, 0.001)
    result["wordsPerMinute"] = word_count / speech_seconds * 60 if speech_seconds > 0 else 0.0

    if len(pitches) >= 3:
        mean = sum(pitches) / len(pitches)
        squares = sum((p - mean) ** 2 for p in pitches)
        result["pitchHz"] = mean
        result["pitchVariation"] = math.sqrt(squares / len(pitches)) / mean
    return result


def analyze(transcript, features, baseline):
    points = {key: 0.0 for key in MOODS}
    points["calm"] = 0.6
    reasons = []
    spoken = words(transcript)
    keyword_hits = 0

    # 1. What was said
    for key in MOODS:
        found = []
        hits = 0
        for phrase in KEYWORDS[key]:
            count = _matches(words(phrase), spoken)
            if count > 0:
                hits += count
                found.append(phrase)
        if hits > 0:
            points[key] += min(hits * 1.5, 4.5)
            keyword_hits += hits
            reasons.append(f"{MOODS[key][0]} words: {', '.join(found)}")

    sentiment = _sentiment(spoken)
    positive = rise(sentiment, 0.1, 0.6)
    negative = rise(-sentiment, 0.1, 0.6)
    points["happy"] += positive * 2
    points["calm"] += positive * 0.5
    points["sad"] += negative * 0.8
    points["lowMood"] += negative * 0.6
    points["worried"] += negative * 0.6
    points["anxious"] += negative * 0.5
    points["angry"] += negative * 0.5
    if sentiment > 0.25:
        reasons.append("Overall wording sounds positive")
    if sentiment < -0.25:
        reasons.append("Overall wording sounds negative")

    # 2. How it sounded, compared with this person's calm voice
    used_baseline = False
    if baseline and features["speechSeconds"] >= 2 and baseline["pitchHz"] > 0:
        used_baseline = True
        loud = features["energyDB"] - baseline["energyDB"]
        pitch = relative(features["pitchHz"], baseline["pitchHz"])
        variation = relative(features["pitchVariation"], baseline["pitchVariation"])
        rate = relative(features["wordsPerMinute"], baseline["wordsPerMinute"])
        pause = features["pauseRatio"] - baseline["pauseRatio"]

        excited = rise(pitch, 0.03, 0.15) * rise(variation, 0.05, 0.35)
        points["happy"] += excited * (0.6 + positive) * 1.5
        points["angry"] += (rise(loud, 2, 8) * 1.5 + rise(rate, 0.05, 0.25) * 0.7) * (0.5 + negative)
        points["anxious"] += (rise(pitch, 0.04, 0.18) * 1.2 + rise(rate, 0.05, 0.25)) * (0.6 + negative * 0.4)
        points["worried"] += rise(pitch, 0.02, 0.10) * 0.8 + rise(pause, 0.03, 0.15) * 0.8
        points["sad"] += rise(-pitch, 0.03, 0.12) + rise(-loud, 1.5, 6)
        points["lowMood"] += (rise(-variation, 0.1, 0.4) * 1.2 + rise(-loud, 1.5, 6) * 0.8
                              + rise(pause, 0.05, 0.2) * 0.8 + rise(-rate, 0.08, 0.25) * 0.6)
        points["tired"] += (rise(-rate, 0.1, 0.3) + rise(-loud, 2, 7) * 0.8) * (1 - negative * 0.5)

        change = max(abs(pitch) / 0.1, abs(loud) / 4, abs(rate) / 0.2)
        points["calm"] += (1 - min(change, 1)) * 1.2

        if abs(loud) >= 3:
            reasons.append(f"Voice {abs(loud):.0f} dB {'louder' if loud > 0 else 'quieter'} than baseline")
        if abs(pitch) >= 0.05:
            reasons.append(f"Pitch {int(abs(pitch) * 100)}% {'higher' if pitch > 0 else 'lower'} than baseline")
        if abs(rate) >= 0.1:
            reasons.append(f"Speaking {int(abs(rate) * 100)}% {'faster' if rate > 0 else 'slower'} than baseline")
        if variation <= -0.2:
            reasons.append("Voice flatter than baseline")
        if pause >= 0.08:
            reasons.append("More pauses than baseline")
    else:
        reasons.append("No voice baseline used, so this is based mostly on words")

    # 3. Pick the strongest result
    ranked = sorted(points.items(), key=lambda item: item[1], reverse=True)
    total = max(sum(value for _, value in ranked), 0.001)
    margin = ranked[0][1] - ranked[1][1]

    if margin >= 1.5 and used_baseline and keyword_hits > 0:
        confidence = "High"
    elif margin >= 0.6 and (used_baseline or keyword_hits > 0):
        confidence = "Medium"
    else:
        confidence = "Low"

    if len(spoken) < 3:
        reasons.append("Very few words were heard")

    return {
        "mood": ranked[0][0],
        "confidence": confidence,
        "scores": [{"mood": key, "share": value / total} for key, value in ranked],
        "reasons": reasons,
        "usedBaseline": used_baseline,
    }


def rms(samples):
    return float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0


def detect_pitch(samples, sample_rate):
    """Finds the voice pitch using autocorrelation, between 75 and 400 Hz."""
    samples = np.asarray(samples, dtype=np.float64)
    step = max(1, int(sample_rate / 12000))
    count = len(samples) // step
    reduced = samples[:count * step].reshape(count, step).mean(axis=1)

    rate = sample_rate / step
    min_lag = int(rate / 400)
    max_lag = int(rate / 75)
    if min_lag <= 1 or count <= max_lag * 2:
        return None

    correlation = np.zeros(max_lag + 2)
    for lag in range(min_lag - 1, max_lag + 2):
        first = reduced[:count - lag]
        second = reduced[lag:]
        divisor = math.sqrt(float(first @ first) * float(second @ second))
        correlation[lag] = float(first @ second) / divisor if divisor > 0 else 0.0

    best = float(correlation[min_lag:max_lag + 1].max())
    if best <= 0.55:
        return None

    # The first strong peak avoids reading double the real period.
    for lag in range(min_lag, max_lag + 1):
        value = correlation[lag]
        if value >= best * 0.9 and value > correlation[lag - 1] and value >= correlation[lag + 1]:
            return rate / lag
    return None
