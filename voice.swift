// Astronaut Voice Log
// Voice-controlled recording with an estimated mood for each log.
//
// HOW TO USE IN SWIFT PLAYGROUNDS (iPad):
//   1. Create a new App project.
//   2. App Settings > Capabilities: add Microphone AND Speech Recognition.
//   3. Replace everything in ContentView.swift with this whole file.
//
// VOICE COMMANDS (after tapping Start Listening):
//   "start log"  "stop log"  "set baseline"  "play last log"
//
// The mood result is an estimate for a school prototype.
// It is NOT a medical or psychological diagnosis.

import SwiftUI
import AVFoundation
import Speech
import NaturalLanguage
import Accelerate

// MARK: - Moods

enum Mood: String, Codable, CaseIterable, Identifiable {
    case happy = "Happy"
    case calm = "Calm"
    case sad = "Sad"
    case lowMood = "Low Mood (possible depression signs)"
    case anxious = "Anxious"
    case worried = "Worried"
    case angry = "Angry"
    case tired = "Tired"

    var id: String { rawValue }

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

    var color: Color {
        switch self {
        case .happy: return .green
        case .calm: return .blue
        case .sad: return .indigo
        case .lowMood: return .purple
        case .anxious: return .orange
        case .worried: return .yellow
        case .angry: return .red
        case .tired: return .gray
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

struct MoodScore: Identifiable, Codable {
    var mood: Mood
    var share: Double
    var id: String { mood.rawValue }
}

struct MoodReading: Codable {
    var mood: Mood
    var confidence: String
    var scores: [MoodScore]
    var reasons: [String]
    var usedBaseline: Bool
}

struct VoiceLog: Identifiable, Codable {
    var id = UUID()
    var date: Date
    var fileName: String
    var transcript: String
    var reading: MoodReading
}

// MARK: - Voice measurements

struct AudioFrame: Sendable {
    var db: Double
    var pitch: Double?
    var seconds: Double
}

struct VoiceFeatures: Codable {
    var energyDB = 0.0
    var pitchHz = 0.0
    var pitchVariation = 0.0
    var wordsPerMinute = 0.0
    var pauseRatio = 0.0
    var speechSeconds = 0.0

    static func make(frames: [AudioFrame], wordCount: Int) -> VoiceFeatures {
        guard !frames.isEmpty else { return VoiceFeatures() }

        // Background noise is estimated from the quietest moments.
        let sortedLevels = frames.map { $0.db }.sorted()
        let noiseFloor = sortedLevels[sortedLevels.count / 10]
        let speechFrames = frames.filter { $0.db > noiseFloor + 8 }
        guard !speechFrames.isEmpty else { return VoiceFeatures() }

        let totalSeconds = frames.reduce(0) { $0 + $1.seconds }
        let speechSeconds = speechFrames.reduce(0) { $0 + $1.seconds }
        let pitches = speechFrames.compactMap { $0.pitch }

        var result = VoiceFeatures()
        result.energyDB = speechFrames.reduce(0) { $0 + $1.db } / Double(speechFrames.count)
        result.speechSeconds = speechSeconds
        result.pauseRatio = 1 - speechSeconds / max(totalSeconds, 0.001)
        result.wordsPerMinute = speechSeconds > 0 ? Double(wordCount) / speechSeconds * 60 : 0

        if pitches.count >= 3 {
            let mean = pitches.reduce(0, +) / Double(pitches.count)
            let squares = pitches.reduce(0) { $0 + ($1 - mean) * ($1 - mean) }
            result.pitchHz = mean
            result.pitchVariation = sqrt(squares / Double(pitches.count)) / mean
        }
        return result
    }
}

// MARK: - Mood estimate

enum MoodAnalyzer {
    static func words(in text: String) -> [String] {
        let allowed = CharacterSet.letters.union(CharacterSet(charactersIn: "'"))
        return text.lowercased()
            .replacingOccurrences(of: "’", with: "'")
            .components(separatedBy: allowed.inverted)
            .filter { !$0.isEmpty }
    }

    static func analyze(transcript: String,
                        features: VoiceFeatures,
                        baseline: VoiceFeatures?) -> MoodReading {
        var points: [Mood: Double] = [:]
        for mood in Mood.allCases { points[mood] = 0 }
        points[.calm] = 0.6

        var reasons: [String] = []
        let spokenWords = words(in: transcript)
        var keywordHits = 0

        // 1. What was said
        for mood in Mood.allCases {
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
                points[mood, default: 0] += min(Double(hits) * 1.5, 4.5)
                keywordHits += hits
                reasons.append("\(mood.rawValue) words: \(found.joined(separator: ", "))")
            }
        }

        let sentiment = sentimentScore(of: transcript)
        let positive = rise(sentiment, 0.1, 0.6)
        let negative = rise(-sentiment, 0.1, 0.6)
        points[.happy, default: 0] += positive * 2
        points[.calm, default: 0] += positive * 0.5
        points[.sad, default: 0] += negative * 0.8
        points[.lowMood, default: 0] += negative * 0.6
        points[.worried, default: 0] += negative * 0.6
        points[.anxious, default: 0] += negative * 0.5
        points[.angry, default: 0] += negative * 0.5
        if sentiment > 0.25 { reasons.append("Overall wording sounds positive") }
        if sentiment < -0.25 { reasons.append("Overall wording sounds negative") }

        // 2. How it sounded, compared with this person's calm voice
        var usedBaseline = false
        if let base = baseline, features.speechSeconds >= 2, base.pitchHz > 0 {
            usedBaseline = true
            let loud = features.energyDB - base.energyDB
            let pitch = relative(features.pitchHz, base.pitchHz)
            let variation = relative(features.pitchVariation, base.pitchVariation)
            let rate = relative(features.wordsPerMinute, base.wordsPerMinute)
            let pause = features.pauseRatio - base.pauseRatio

            let excited = rise(pitch, 0.03, 0.15) * rise(variation, 0.05, 0.35)
            points[.happy, default: 0] += excited * (0.6 + positive) * 1.5
            points[.angry, default: 0] += (rise(loud, 2, 8) * 1.5 + rise(rate, 0.05, 0.25) * 0.7)
                * (0.5 + negative)
            points[.anxious, default: 0] += (rise(pitch, 0.04, 0.18) * 1.2 + rise(rate, 0.05, 0.25))
                * (0.6 + negative * 0.4)
            points[.worried, default: 0] += rise(pitch, 0.02, 0.10) * 0.8 + rise(pause, 0.03, 0.15) * 0.8
            points[.sad, default: 0] += rise(-pitch, 0.03, 0.12) + rise(-loud, 1.5, 6)
            points[.lowMood, default: 0] += rise(-variation, 0.1, 0.4) * 1.2
                + rise(-loud, 1.5, 6) * 0.8
                + rise(pause, 0.05, 0.2) * 0.8
                + rise(-rate, 0.08, 0.25) * 0.6
            points[.tired, default: 0] += (rise(-rate, 0.1, 0.3) + rise(-loud, 2, 7) * 0.8)
                * (1 - negative * 0.5)

            let change = max(abs(pitch) / 0.1, abs(loud) / 4, abs(rate) / 0.2)
            points[.calm, default: 0] += (1 - min(change, 1)) * 1.2

            if abs(loud) >= 3 {
                reasons.append(String(format: "Voice %.0f dB %@ than baseline",
                                      abs(loud), loud > 0 ? "louder" : "quieter"))
            }
            if abs(pitch) >= 0.05 {
                reasons.append("Pitch \(Int(abs(pitch) * 100))% \(pitch > 0 ? "higher" : "lower") than baseline")
            }
            if abs(rate) >= 0.1 {
                reasons.append("Speaking \(Int(abs(rate) * 100))% \(rate > 0 ? "faster" : "slower") than baseline")
            }
            if variation <= -0.2 {
                reasons.append("Voice flatter than baseline")
            }
            if pause >= 0.08 {
                reasons.append("More pauses than baseline")
            }
        } else {
            reasons.append("No voice baseline used, so this is based mostly on words")
        }

        // 3. Pick the strongest result
        let ranked = points.sorted { $0.value > $1.value }
        let total = max(ranked.reduce(0) { $0 + $1.value }, 0.001)
        let top = ranked[0]
        let margin = top.value - ranked[1].value

        let confidence: String
        if margin >= 1.5 && usedBaseline && keywordHits > 0 {
            confidence = "High"
        } else if margin >= 0.6 && (usedBaseline || keywordHits > 0) {
            confidence = "Medium"
        } else {
            confidence = "Low"
        }

        if spokenWords.count < 3 {
            reasons.append("Very few words were heard")
        }

        return MoodReading(
            mood: top.key,
            confidence: confidence,
            scores: ranked.map { MoodScore(mood: $0.key, share: $0.value / total) },
            reasons: reasons,
            usedBaseline: usedBaseline
        )
    }

    // Counts a phrase, skipping cases such as "not happy" or "never really happy".
    private static func matches(of phrase: [String], in spoken: [String]) -> Int {
        guard !phrase.isEmpty, spoken.count >= phrase.count else { return 0 }
        let negators: Set<String> = ["not", "never", "no", "isn't", "wasn't", "aren't",
                                     "don't", "didn't", "won't", "without"]
        var count = 0
        for start in 0...(spoken.count - phrase.count) {
            guard Array(spoken[start..<(start + phrase.count)]) == phrase else { continue }
            let before = spoken[max(0, start - 2)..<start]
            if !before.contains(where: { negators.contains($0) }) {
                count += 1
            }
        }
        return count
    }

    private static func sentimentScore(of text: String) -> Double {
        guard !text.isEmpty else { return 0 }
        let tagger = NLTagger(tagSchemes: [.sentimentScore])
        tagger.string = text
        let (tag, _) = tagger.tag(at: text.startIndex, unit: .paragraph, scheme: .sentimentScore)
        return Double(tag?.rawValue ?? "0") ?? 0
    }

    private static func rise(_ value: Double, _ low: Double, _ high: Double) -> Double {
        min(max((value - low) / (high - low), 0), 1)
    }

    private static func relative(_ value: Double, _ base: Double) -> Double {
        (value > 0 && base > 0) ? value / base - 1 : 0
    }
}

// MARK: - Permissions

private func askForMicrophone() async -> Bool {
    await withCheckedContinuation { continuation in
        AVAudioSession.sharedInstance().requestRecordPermission { granted in
            continuation.resume(returning: granted)
        }
    }
}

private func askForSpeechRecognition() async -> Bool {
    await withCheckedContinuation { continuation in
        SFSpeechRecognizer.requestAuthorization { status in
            continuation.resume(returning: status == .authorized)
        }
    }
}

// MARK: - Audio pipeline (microphone, speech recognition, file saving)

enum PipelineError: LocalizedError {
    case speechUnavailable
    case microphoneUnavailable

    var errorDescription: String? {
        switch self {
        case .speechUnavailable: return "Speech recognition is not available right now."
        case .microphoneUnavailable: return "No microphone input was found."
        }
    }
}

// Audio arrives on a background thread, so this class is kept off the main actor.
final class AudioPipeline: @unchecked Sendable {
    var onSpeech: (@MainActor @Sendable (String, Bool) -> Void)?

    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let lock = NSLock()

    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var generation = 0
    private var running = false
    private var inputFormat: AVAudioFormat?

    private var capturing = false
    private var audioFile: AVAudioFile?
    private var frames: [AudioFrame] = []

    var worksOffline: Bool {
        recognizer?.supportsOnDeviceRecognition ?? false
    }

    func start() throws {
        guard let recognizer = recognizer, recognizer.isAvailable else {
            throw PipelineError.speechUnavailable
        }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement)
        try session.setActive(true)

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw PipelineError.microphoneUnavailable
        }

        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            self?.process(buffer)
        }

        engine.prepare()
        try engine.start()

        lock.lock()
        running = true
        inputFormat = format
        lock.unlock()

        restartRecognition()
    }

    func stop() {
        lock.lock()
        running = false
        generation += 1
        capturing = false
        audioFile = nil
        let oldRequest = request
        let oldTask = task
        request = nil
        task = nil
        lock.unlock()

        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        oldRequest?.endAudio()
        oldTask?.cancel()
    }

    func restartRecognition() {
        guard let recognizer = recognizer else { return }

        let newRequest = SFSpeechAudioBufferRecognitionRequest()
        newRequest.shouldReportPartialResults = true
        newRequest.contextualStrings = ["start log", "stop log", "set baseline", "play last log"]
        if recognizer.supportsOnDeviceRecognition {
            newRequest.requiresOnDeviceRecognition = true
        }

        lock.lock()
        guard running else {
            lock.unlock()
            return
        }
        generation += 1
        let thisGeneration = generation
        let oldRequest = request
        let oldTask = task
        request = newRequest
        lock.unlock()

        oldRequest?.endAudio()
        oldTask?.cancel()

        let newTask = recognizer.recognitionTask(with: newRequest) { [weak self] result, error in
            self?.handleRecognition(result: result, error: error, generation: thisGeneration)
        }

        lock.lock()
        if generation == thisGeneration {
            task = newTask
            lock.unlock()
        } else {
            lock.unlock()
            newTask.cancel()
        }
    }

    func beginCapture(saveTo url: URL?) throws {
        lock.lock()
        let format = inputFormat
        lock.unlock()
        guard let format = format else { throw PipelineError.microphoneUnavailable }

        var file: AVAudioFile?
        if let url = url {
            let settings: [String: Any] = [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: format.sampleRate,
                AVNumberOfChannelsKey: Int(format.channelCount),
                AVEncoderBitRateKey: 64_000
            ]
            file = try AVAudioFile(forWriting: url,
                                   settings: settings,
                                   commonFormat: format.commonFormat,
                                   interleaved: format.isInterleaved)
        }

        lock.lock()
        audioFile = file
        frames = []
        capturing = true
        lock.unlock()
    }

    // Stops saving and closes the file. Listening continues.
    func endCapture() -> [AudioFrame] {
        lock.lock()
        capturing = false
        audioFile = nil
        let captured = frames
        frames = []
        lock.unlock()
        return captured
    }

    private func handleRecognition(result: SFSpeechRecognitionResult?, error: Error?, generation thisGeneration: Int) {
        lock.lock()
        let isCurrent = thisGeneration == generation && running
        lock.unlock()
        guard isCurrent else { return }

        if let result = result {
            report(result.bestTranscription.formattedString, ended: result.isFinal)
            if result.isFinal {
                restartRecognition()
            }
        } else if error != nil {
            // Silence also ends up here, so wait briefly and listen again.
            report("", ended: true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
                self?.restartIfStillCurrent(thisGeneration)
            }
        }
    }

    private func restartIfStillCurrent(_ thisGeneration: Int) {
        lock.lock()
        let isCurrent = thisGeneration == generation && running
        lock.unlock()
        if isCurrent {
            restartRecognition()
        }
    }

    private func report(_ text: String, ended: Bool) {
        guard let callback = onSpeech else { return }
        Task { @MainActor in
            callback(text, ended)
        }
    }

    private func process(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        let currentRequest = request
        let file = audioFile
        let isCapturing = capturing
        lock.unlock()

        currentRequest?.append(buffer)
        guard isCapturing else { return }

        try? file?.write(from: buffer)

        guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return }
        let count = Int(buffer.frameLength)
        let sampleRate = buffer.format.sampleRate

        var rms: Float = 0
        vDSP_rmsqv(samples, 1, &rms, vDSP_Length(count))

        let frame = AudioFrame(
            db: 20 * log10(Double(max(rms, 0.000001))),
            pitch: Self.detectPitch(samples: samples, count: count, sampleRate: sampleRate),
            seconds: Double(count) / sampleRate
        )

        lock.lock()
        if capturing {
            frames.append(frame)
        }
        lock.unlock()
    }

    // Finds the voice pitch using autocorrelation, between 75 and 400 Hz.
    private static func detectPitch(samples: UnsafePointer<Float>, count: Int, sampleRate: Double) -> Double? {
        let step = max(1, Int(sampleRate / 12_000))
        var reduced: [Float] = []
        reduced.reserveCapacity(count / step + 1)

        var position = 0
        while position + step <= count {
            var mean: Float = 0
            vDSP_meanv(samples + position, 1, &mean, vDSP_Length(step))
            reduced.append(mean)
            position += step
        }

        let rate = sampleRate / Double(step)
        let minLag = Int(rate / 400)
        let maxLag = Int(rate / 75)
        guard minLag > 1, reduced.count > maxLag * 2 else { return nil }

        var correlation = [Float](repeating: 0, count: maxLag + 2)
        let total = reduced.count

        reduced.withUnsafeBufferPointer { pointer in
            guard let x = pointer.baseAddress else { return }
            for lag in (minLag - 1)...(maxLag + 1) {
                let length = vDSP_Length(total - lag)
                var both: Float = 0
                var first: Float = 0
                var second: Float = 0
                vDSP_dotpr(x, 1, x + lag, 1, &both, length)
                vDSP_dotpr(x, 1, x, 1, &first, length)
                vDSP_dotpr(x + lag, 1, x + lag, 1, &second, length)
                let divisor = (first * second).squareRoot()
                correlation[lag] = divisor > 0 ? both / divisor : 0
            }
        }

        var best: Float = 0
        for lag in minLag...maxLag where correlation[lag] > best {
            best = correlation[lag]
        }
        guard best > 0.55 else { return nil }

        // The first strong peak avoids reading double the real period.
        for lag in minLag...maxLag {
            let value = correlation[lag]
            if value >= best * 0.9, value > correlation[lag - 1], value >= correlation[lag + 1] {
                return rate / Double(lag)
            }
        }
        return nil
    }
}

// MARK: - App logic

@MainActor
final class MissionVoiceModel: ObservableObject {
    enum Mode {
        case idle, listening, recording, calibrating, playing
    }

    @Published var mode: Mode = .idle
    @Published var status = "Tap Start Listening"
    @Published var liveText = ""
    @Published var logs: [VoiceLog] = []
    @Published var baseline: VoiceFeatures?

    private let pipeline = AudioPipeline()
    private var player: AVAudioPlayer?

    private var committedText = ""
    private var partialText = ""
    private var currentFileName = ""
    private var calibrationToken = 0
    private var playbackToken = 0
    private var resumeAfterPlayback = false

    private let baselineKey = "voiceBaseline"

    private var folder: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    private var logsFile: URL {
        folder.appendingPathComponent("logs.json")
    }

    private var fullText: String {
        (committedText + " " + partialText).trimmingCharacters(in: .whitespaces)
    }

    var speechPrivacyNote: String {
        pipeline.worksOffline
            ? "Speech is processed on this iPad"
            : "This iPad needs internet for speech recognition"
    }

    init() {
        pipeline.onSpeech = { [weak self] text, ended in
            self?.handleSpeech(text, ended: ended)
        }

        if let data = try? Data(contentsOf: logsFile),
           let saved = try? JSONDecoder().decode([VoiceLog].self, from: data) {
            logs = saved
        }
        if let data = UserDefaults.standard.data(forKey: baselineKey),
           let saved = try? JSONDecoder().decode(VoiceFeatures.self, from: data) {
            baseline = saved
        }
    }

    // MARK: Listening

    func startListening() async {
        guard mode == .idle else { return }

        guard await askForMicrophone() else {
            status = "Microphone is off. Turn it on in Settings."
            return
        }
        guard await askForSpeechRecognition() else {
            status = "Speech Recognition is off. Turn it on in Settings."
            return
        }

        do {
            try pipeline.start()
            clearSpeech()
            mode = .listening
            status = "Listening. Say \"start log\"."
        } catch {
            status = "Error: \(error.localizedDescription)"
        }
    }

    func stopListening() {
        guard mode == .listening else { return }
        pipeline.stop()
        clearSpeech()
        mode = .idle
        status = "Tap Start Listening"
    }

    func mainButtonTapped() async {
        switch mode {
        case .idle:
            await startListening()
            if mode == .listening {
                startLog()
            }
        case .listening:
            startLog()
        case .recording:
            stopLog(spokenText: fullText)
        case .calibrating:
            finishBaseline()
        case .playing:
            await finishPlayback()
        }
    }

    private func clearSpeech() {
        committedText = ""
        partialText = ""
        liveText = ""
    }

    private func handleSpeech(_ text: String, ended: Bool) {
        guard mode == .listening || mode == .recording || mode == .calibrating else { return }

        if !text.isEmpty {
            partialText = text
        }
        let spoken = " " + MoodAnalyzer.words(in: text).joined(separator: " ") + " "

        switch mode {
        case .listening:
            liveText = partialText
            if spoken.contains(" start log ") || spoken.contains(" begin log ") || spoken.contains(" start blog ") {
                startLog()
                return
            }
            if spoken.contains(" set baseline ") {
                startBaseline()
                return
            }
            if spoken.contains(" play last log "), let newest = logs.first {
                play(newest)
                return
            }
            if ended {
                clearSpeech()
            }

        case .recording:
            liveText = fullText
            for command in ["stop log", "end log", "stop blog"] where spoken.contains(" \(command) ") {
                stopLog(spokenText: textBefore(command: command))
                return
            }
            if ended {
                committedText = fullText
                partialText = ""
            }

        case .calibrating:
            liveText = fullText
            if ended {
                committedText = fullText
                partialText = ""
            }

        default:
            break
        }
    }

    private func textBefore(command: String) -> String {
        let text = fullText
        guard let range = text.range(of: command, options: [.caseInsensitive, .backwards]) else {
            return text
        }
        return String(text[..<range.lowerBound]).trimmingCharacters(in: .whitespaces)
    }

    // MARK: Logs

    func startLog() {
        guard mode == .listening else { return }

        let name = "Log_\(gmtText(Date(), pattern: "yyyy-MM-dd_HH-mm-ss"))_GMT.m4a"
        do {
            try pipeline.beginCapture(saveTo: folder.appendingPathComponent(name))
            currentFileName = name
            clearSpeech()
            pipeline.restartRecognition()
            mode = .recording
            status = "Recording. Say \"stop log\" when finished."
        } catch {
            status = "Error: \(error.localizedDescription)"
        }
    }

    private func stopLog(spokenText: String) {
        guard mode == .recording else { return }

        let frames = pipeline.endCapture()
        let wordCount = MoodAnalyzer.words(in: spokenText).count
        let features = VoiceFeatures.make(frames: frames, wordCount: wordCount)
        let reading = MoodAnalyzer.analyze(transcript: spokenText, features: features, baseline: baseline)

        logs.insert(VoiceLog(date: Date(),
                             fileName: currentFileName,
                             transcript: spokenText,
                             reading: reading), at: 0)
        saveLogs()

        clearSpeech()
        pipeline.restartRecognition()
        mode = .listening
        status = "Saved. Listening for \"start log\"."
    }

    func delete(at offsets: IndexSet) {
        for index in offsets {
            try? FileManager.default.removeItem(at: folder.appendingPathComponent(logs[index].fileName))
        }
        logs.remove(atOffsets: offsets)
        saveLogs()
    }

    private func saveLogs() {
        if let data = try? JSONEncoder().encode(logs) {
            try? data.write(to: logsFile, options: .atomic)
        }
    }

    // MARK: Baseline

    func startBaseline() {
        guard mode == .listening else { return }

        do {
            try pipeline.beginCapture(saveTo: nil)
            clearSpeech()
            pipeline.restartRecognition()
            mode = .calibrating
            status = "Read the sentence calmly for 20 seconds."

            calibrationToken += 1
            let token = calibrationToken
            Task {
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                if token == self.calibrationToken {
                    self.finishBaseline()
                }
            }
        } catch {
            status = "Error: \(error.localizedDescription)"
        }
    }

    private func finishBaseline() {
        guard mode == .calibrating else { return }
        calibrationToken += 1

        let frames = pipeline.endCapture()
        let wordCount = MoodAnalyzer.words(in: fullText).count
        let features = VoiceFeatures.make(frames: frames, wordCount: wordCount)

        clearSpeech()
        pipeline.restartRecognition()
        mode = .listening

        guard features.speechSeconds >= 5, features.pitchHz > 0 else {
            status = "Not enough clear speech. Please try the baseline again."
            return
        }

        baseline = features
        if let data = try? JSONEncoder().encode(features) {
            UserDefaults.standard.set(data, forKey: baselineKey)
        }
        status = "Baseline saved. Listening."
    }

    // MARK: Playback

    func play(_ log: VoiceLog) {
        guard mode == .idle || mode == .listening || mode == .playing else {
            status = "Finish the current recording first."
            return
        }

        // Listening pauses so the app does not hear its own playback.
        if mode == .listening {
            resumeAfterPlayback = true
            pipeline.stop()
            clearSpeech()
        } else if mode == .idle {
            resumeAfterPlayback = false
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)

            let newPlayer = try AVAudioPlayer(contentsOf: folder.appendingPathComponent(log.fileName))
            player = newPlayer
            newPlayer.play()
            mode = .playing
            status = "Playing log"

            playbackToken += 1
            let token = playbackToken
            let wait = UInt64((newPlayer.duration + 0.3) * 1_000_000_000)
            Task {
                try? await Task.sleep(nanoseconds: wait)
                if token == self.playbackToken {
                    await self.finishPlayback()
                }
            }
        } catch {
            status = "Could not play: \(error.localizedDescription)"
            mode = .playing
            Task { await self.finishPlayback() }
        }
    }

    private func finishPlayback() async {
        guard mode == .playing else { return }

        playbackToken += 1
        player?.stop()
        player = nil
        mode = .idle
        status = "Tap Start Listening"

        if resumeAfterPlayback {
            resumeAfterPlayback = false
            await startListening()
        }
    }
}

private func gmtText(_ date: Date, pattern: String) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(identifier: "GMT")
    formatter.dateFormat = pattern
    return formatter.string(from: date)
}

// MARK: - Screen

struct ContentView: View {
    @StateObject private var voice = MissionVoiceModel()

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            VStack(spacing: 16) {
                header
                mainButton
                listeningControls
                liveSpeech
                if let latest = voice.logs.first {
                    moodCard(latest.reading)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity)

            logList
                .frame(maxWidth: .infinity)
        }
        .padding()
    }

    private var header: some View {
        VStack(spacing: 4) {
            Text("Astronaut Voice Log").font(.largeTitle.bold())
            Text(voice.status).font(.headline)
            Text(voice.speechPrivacyNote).font(.caption).foregroundColor(.secondary)
        }
    }

    private var mainButtonTitle: String {
        switch voice.mode {
        case .idle, .listening: return "START LOG"
        case .recording: return "STOP LOG"
        case .calibrating: return "FINISH BASELINE"
        case .playing: return "STOP PLAYBACK"
        }
    }

    private var mainButton: some View {
        Button {
            Task { await voice.mainButtonTapped() }
        } label: {
            Text(mainButtonTitle)
                .font(.system(size: 40, weight: .bold))
                .foregroundColor(.white)
                .frame(maxWidth: .infinity, minHeight: 150)
                .background(voice.mode == .recording ? Color.red : Color.blue)
                .cornerRadius(24)
        }
    }

    private var listeningControls: some View {
        VStack(spacing: 8) {
            HStack {
                if voice.mode == .idle {
                    Button("Start Listening") {
                        Task { await voice.startListening() }
                    }
                } else if voice.mode == .listening {
                    Button("Stop Listening") {
                        voice.stopListening()
                    }
                }

                Spacer()

                Button(voice.baseline == nil ? "Set Calm Baseline" : "Redo Calm Baseline") {
                    voice.startBaseline()
                }
                .disabled(voice.mode != .listening)
            }

            if voice.mode == .calibrating {
                Text("Read calmly: \"Today I checked the station systems, ate breakfast, and looked out at the Earth below. Everything is running as planned, and I am ready for the next task.\"")
                    .font(.title3)
            } else {
                Text("Say: \"start log\", \"stop log\", \"set baseline\", \"play last log\"")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private var liveSpeech: some View {
        Text(voice.liveText.isEmpty ? "Words you say appear here" : voice.liveText)
            .foregroundColor(voice.liveText.isEmpty ? .secondary : .primary)
            .lineLimit(4)
            .frame(maxWidth: .infinity, minHeight: 70, alignment: .topLeading)
            .padding(10)
            .background(Color.gray.opacity(0.15))
            .cornerRadius(12)
    }

    private func moodCard(_ reading: MoodReading) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Latest estimated mood").font(.caption).foregroundColor(.secondary)
            Text("\(reading.mood.emoji) \(reading.mood.rawValue)")
                .font(.title.bold())
                .foregroundColor(reading.mood.color)
            Text("Confidence: \(reading.confidence)").font(.subheadline)

            ForEach(reading.scores.prefix(3)) { score in
                HStack {
                    Text(score.mood.emoji)
                    ProgressView(value: score.share)
                        .tint(score.mood.color)
                }
            }

            if reading.mood == .lowMood || reading.mood == .sad {
                Text("If this feeling keeps coming back, talk with the flight surgeon or someone you trust.")
                    .font(.subheadline)
            }

            Text("Estimate only. Not a medical diagnosis.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(reading.mood.color.opacity(0.12))
        .cornerRadius(16)
    }

    private var logList: some View {
        List {
            ForEach(voice.logs) { log in
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(log.transcript.isEmpty ? "(No words heard)" : log.transcript)
                        Text("Confidence: \(log.reading.confidence)").font(.subheadline)

                        ForEach(log.reading.reasons, id: \.self) { reason in
                            Text("• \(reason)").font(.caption)
                        }

                        Button("Play") {
                            voice.play(log)
                        }
                        .buttonStyle(.borderless)
                    }
                } label: {
                    HStack {
                        Text(log.reading.mood.emoji).font(.title)
                        VStack(alignment: .leading) {
                            Text(log.reading.mood.rawValue).font(.headline)
                            Text(gmtText(log.date, pattern: "yyyy-MM-dd HH:mm:ss") + " GMT")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .onDelete { voice.delete(at: $0) }
        }
    }
}
