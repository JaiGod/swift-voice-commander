// Astronaut Voice Log (Lite)
// A typed version for older Swift apps that do not have AVFoundation or Speech.
// There is no microphone here. Type a log and it estimates the mood from the words.
//
// HOW TO USE: replace all of your code with this whole file, then run it.
//   - If your app has SwiftUI, this makes a screen called ContentView.
//   - If it does not, this prints results. Edit sampleLogs at the bottom to test your own.
//
// The mood result is an estimate for a school prototype.
// It is NOT a medical or psychological diagnosis.

#if canImport(SwiftUI)
import SwiftUI
#endif

// MARK: - Moods

enum Mood: String, CaseIterable {
    case happy = "Happy"
    case calm = "Calm"
    case sad = "Sad"
    case lowMood = "Low Mood (possible depression signs)"
    case anxious = "Anxious"
    case worried = "Worried"
    case angry = "Angry"
    case tired = "Tired"

    var emoji: String {
        switch self {
        case .happy: return "😀"
        case .calm: return "😌"
        case .sad: return "😢"
        case .lowMood: return "😞"
        case .anxious: return "😰"
        case .worried: return "😟"
        case .angry: return "😠"
        case .tired: return "😴"
        }
    }

    // Words and short phrases linked to each mood. Add more to improve it.
    static let keywords: [Mood: [String]] = [
        .happy: ["happy", "great", "excited", "wonderful", "amazing", "glad",
                 "joy", "love this", "fantastic", "proud", "grateful", "good day"],
        .calm: ["calm", "relaxed", "peaceful", "fine", "steady", "okay", "comfortable"],
        .sad: ["sad", "crying", "cry", "miss home", "miss my family", "lonely",
               "homesick", "upset", "heartbroken", "alone"],
        .lowMood: ["depressed", "hopeless", "empty", "worthless", "numb",
                   "no energy", "pointless", "can't go on", "nothing matters",
                   "don't care anymore", "give up"],
        .anxious: ["anxious", "panic", "panicking", "nervous", "scared", "afraid",
                   "terrified", "heart racing", "can't breathe", "overwhelmed", "on edge"],
        .worried: ["worried", "worry", "worrying", "concerned", "unsure", "what if",
                   "not sure", "problem", "trouble", "uneasy"],
        .angry: ["angry", "furious", "mad", "annoyed", "frustrated", "irritated",
                 "hate", "fed up", "unacceptable", "sick of"],
        .tired: ["tired", "exhausted", "sleepy", "fatigued", "drained", "worn out",
                 "can't sleep", "no sleep", "need rest"]
    ]
}

struct MoodScore {
    var mood: Mood
    var share: Double
}

struct MoodReading {
    var mood: Mood
    var confidence: String
    var scores: [MoodScore]
    var reasons: [String]
}

// MARK: - Mood estimate

enum MoodAnalyzer {
    static func words(in text: String) -> [String] {
        var result: [String] = []
        var current = ""
        for character in text.lowercased() {
            if character.isLetter || character == "'" || character == "’" {
                current.append(character == "’" ? "'" : character)
            } else if !current.isEmpty {
                result.append(current)
                current = ""
            }
        }
        if !current.isEmpty {
            result.append(current)
        }
        return result
    }

    static func analyze(transcript: String) -> MoodReading {
        var results: [(mood: Mood, points: Double)] = []
        var reasons: [String] = []
        var keywordHits = 0
        let spokenWords = words(in: transcript)

        for mood in Mood.allCases {
            var points: Double = mood == .calm ? 0.6 : 0
            var found: [String] = []
            var hits = 0

            for phrase in Mood.keywords[mood] ?? [] {
                let count = matches(of: words(in: phrase), in: spokenWords)
                if count > 0 {
                    hits += count
                    found.append(phrase)
                }
            }

            if hits > 0 {
                points += min(Double(hits) * 1.5, 4.5)
                keywordHits += hits
                reasons.append(mood.rawValue + " words: " + found.joined(separator: ", "))
            }
            results.append((mood: mood, points: points))
        }

        if spokenWords.count < 3 {
            reasons.append("Very few words were entered")
        }
        if keywordHits == 0 {
            reasons.append("No mood words were found")
        }

        let ranked = results.sorted { $0.points > $1.points }
        var total = 0.0
        for item in ranked {
            total += item.points
        }

        let margin = ranked[0].points - ranked[1].points
        let confidence = (margin >= 0.6 && keywordHits > 0) ? "Medium" : "Low"

        return MoodReading(
            mood: ranked[0].mood,
            confidence: confidence,
            scores: ranked.map { MoodScore(mood: $0.mood, share: $0.points / total) },
            reasons: reasons
        )
    }

    // Counts a phrase, skipping cases such as "not happy" or "never really happy".
    private static func matches(of phrase: [String], in spoken: [String]) -> Int {
        if phrase.isEmpty || spoken.count < phrase.count {
            return 0
        }
        let negators: Set<String> = ["not", "never", "no", "isn't", "wasn't", "aren't",
                                     "don't", "didn't", "won't", "without"]
        var count = 0
        for start in 0...(spoken.count - phrase.count) {
            if Array(spoken[start..<(start + phrase.count)]) != phrase {
                continue
            }
            let before = spoken[max(0, start - 2)..<start]
            if !before.contains(where: { negators.contains($0) }) {
                count += 1
            }
        }
        return count
    }
}

#if canImport(SwiftUI)

// MARK: - Screen

struct LogEntry: Identifiable {
    var id: Int
    var text: String
    var reading: MoodReading
}

struct ContentView: View {
    @State private var draft = ""
    @State private var logs: [LogEntry] = []
    @State private var nextID = 0

    var body: some View {
        VStack(spacing: 16) {
            Text("Astronaut Voice Log").font(.largeTitle).bold()
            Text("Type a log, then tap Save Log").font(.headline)

            TextField("How are you feeling today?", text: $draft)
                .textFieldStyle(RoundedBorderTextFieldStyle())

            Button(action: saveLog) {
                Text("Save Log").font(.title).bold()
            }

            if !logs.isEmpty {
                latestCard(logs[0].reading)
            }

            List(logs) { log in
                VStack(alignment: .leading, spacing: 4) {
                    Text(log.reading.mood.emoji + " " + log.reading.mood.rawValue).font(.headline)
                    Text(log.text)
                    Text("Confidence: " + log.reading.confidence).font(.caption)
                }
            }
        }
        .padding()
    }

    private func saveLog() {
        if MoodAnalyzer.words(in: draft).isEmpty {
            return
        }
        let entry = LogEntry(id: nextID, text: draft, reading: MoodAnalyzer.analyze(transcript: draft))
        logs.insert(entry, at: 0)
        nextID += 1
        draft = ""
    }

    private func latestCard(_ reading: MoodReading) -> some View {
        VStack(spacing: 6) {
            Text("Latest estimated mood").font(.caption)
            Text(reading.mood.emoji + " " + reading.mood.rawValue).font(.title).bold()
            Text("Confidence: " + reading.confidence)
            Text(reading.reasons.joined(separator: "\n")).font(.caption)
            Text("Estimate only. Not a medical diagnosis.").font(.caption)
        }
    }
}

#else

// MARK: - Printed results

let sampleLogs = [
    "I am so happy and proud of the crew today",
    "I feel tired and worn out and I need rest",
    "I am not happy and I miss home"
]

for log in sampleLogs {
    let reading = MoodAnalyzer.analyze(transcript: log)
    print("Log: " + log)
    print("Mood: " + reading.mood.emoji + " " + reading.mood.rawValue)
    print("Confidence: " + reading.confidence)
    for reason in reading.reasons {
        print(" - " + reason)
    }
    print("Estimate only. Not a medical diagnosis.")
    print("")
}

#endif
