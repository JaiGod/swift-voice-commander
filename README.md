# Astronaut Voice Log

A voice-controlled iPad app for recording spoken logs. Each log is transcribed and given an estimated mood, based on the words used and how the speaker's voice compares with their own calm baseline.

This is a school prototype built with SwiftUI in a single file, [`voice.swift`](voice.swift).

> **The mood result is only an estimate. It is not a medical or psychological diagnosis.**

## Features

- Hands-free control with spoken commands
- Live transcription while you speak
- Saved audio recordings that can be played back in the app
- Eight estimated moods: Happy, Calm, Sad, Low Mood, Anxious, Worried, Angry and Tired
- A confidence rating and the reasons behind each estimate
- A personal calm-voice baseline for comparing loudness, pitch, speaking rate and pauses

## Running the app

The app uses Apple-only frameworks, so it needs an Apple device or a Mac. It will not build on Windows or Linux.

### Swift Playgrounds on iPad

1. Create a new App project.
2. Open App Settings > Capabilities and add **Microphone** and **Speech Recognition**.
3. Replace everything in `ContentView.swift` with the contents of `voice.swift`.
4. Run the app and allow microphone and speech recognition access.

## Voice commands

Tap **Start Listening**, then say:

| Say | What happens |
| --- | --- |
| "start log" or "begin log" | Starts a new recording |
| "stop log" or "end log" | Stops recording, then saves the log and its mood estimate |
| "set baseline" | Starts a 20-second calm-voice calibration |
| "play last log" | Plays the newest recording |

You can also use the on-screen buttons. Set a baseline first for better results. Without one, the estimate relies mostly on the words spoken.

## How the mood estimate works

Each log gets points for every mood from three sources:

1. **Keywords:** words and phrases linked to each mood, skipping negated cases such as "not happy".
2. **Sentiment:** the overall positive or negative tone from Apple's NaturalLanguage framework.
3. **Voice:** changes in loudness, pitch, pitch variation, speaking rate and pauses compared with the baseline.

The mood with the most points is shown. Confidence depends on how far it leads the next mood, and on whether keywords and a baseline were available.

To tune the results, edit the phrases in `Mood.keywords`.

## Privacy

- Recordings, transcripts and the baseline are stored locally in the app.
- Speech is processed on the device when the iPad supports it. Otherwise, it uses Apple's online speech recognition and needs an internet connection. The app shows which applies.
