import Flutter
import UIKit
@preconcurrency import AVFoundation
@preconcurrency import Speech

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private var speechChannelHandler: SpeechChannelHandler?
  private var speechRecognitionChannelHandler: SpeechRecognitionChannelHandler?
  private var pcmSpeechChannelHandler: PcmSpeechChannelHandler?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    speechChannelHandler = SpeechChannelHandler(
      messenger: engineBridge.applicationRegistrar.messenger()
    )
    speechRecognitionChannelHandler = SpeechRecognitionChannelHandler(
      messenger: engineBridge.applicationRegistrar.messenger()
    )
    pcmSpeechChannelHandler = PcmSpeechChannelHandler(
      messenger: engineBridge.applicationRegistrar.messenger()
    )
  }
}

private final class PcmSpeechChannelHandler: NSObject, @unchecked Sendable {
  private let channel: FlutterMethodChannel
  private let audioEngine = AVAudioEngine()
  private let player = AVAudioPlayerNode()
  private var format: AVAudioFormat?
  private var activeTurnId: String?
  private var generation = UUID()
  private var scheduledBufferCount = 0
  private var completedBufferCount = 0
  private var scheduledSampleCount: Int64 = 0
  private var completedTurnId: String?
  private var completedSampleCount: Int64 = 0
  private var finishResult: FlutterResult?
  private var fadeResult: FlutterResult?

  init(messenger: FlutterBinaryMessenger) {
    channel = FlutterMethodChannel(name: "ava/pcm_speech", binaryMessenger: messenger)
    super.init()
    audioEngine.attach(player)
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self else {
        result(FlutterError(
          code: "pcm_speech_unavailable",
          message: "The iOS PCM speech handler is unavailable.",
          details: nil
        ))
        return
      }
      switch call.method {
      case "start":
        self.start(call.arguments, result: result)
      case "append":
        self.append(call.arguments, result: result)
      case "finish":
        self.finish(call.arguments, result: result)
      case "position":
        self.position(call.arguments, result: result)
      case "progress":
        self.progress(call.arguments, result: result)
      case "fadeOutAndStop":
        self.fadeOutAndStop(call.arguments, result: result)
      case "stop":
        self.stop()
        result(nil)
      default:
        result(FlutterMethodNotImplemented)
      }
    }
  }

  private func start(_ arguments: Any?, result: @escaping FlutterResult) {
    guard activeTurnId == nil else {
      result(flutterError(
        "pcm_speech_busy",
        "Ava is already playing a speech turn."
      ))
      return
    }
    guard
      let values = arguments as? [String: Any],
      let turnId = validTurnId(values["turnId"]),
      let sampleRate = values["sampleRateHz"] as? NSNumber,
      sampleRate.intValue == 24_000,
      let channels = values["channels"] as? NSNumber,
      channels.intValue == 1,
      let bits = values["bitsPerSample"] as? NSNumber,
      bits.intValue == 16,
      let volume = values["volume"] as? NSNumber,
      (0.0...1.0).contains(volume.doubleValue),
      let streamFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: sampleRate.doubleValue,
        channels: 1,
        interleaved: false
      )
    else {
      result(flutterError(
        "invalid_pcm_speech_start",
        "PCM speech requires a valid turn, mono 24 kHz PCM16, and volume."
      ))
      return
    }

    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
      try session.setActive(true)
      audioEngine.connect(player, to: audioEngine.mainMixerNode, format: streamFormat)
      audioEngine.prepare()
      try audioEngine.start()
      player.volume = volume.floatValue
      player.play()
    } catch {
      player.stop()
      audioEngine.stop()
      audioEngine.disconnectNodeOutput(player)
      result(flutterError(
        "pcm_speech_start_failed",
        "Could not start Ava's PCM speech: \(error.localizedDescription)"
      ))
      return
    }

    format = streamFormat
    activeTurnId = turnId
    completedTurnId = nil
    completedSampleCount = 0
    generation = UUID()
    scheduledBufferCount = 0
    completedBufferCount = 0
    scheduledSampleCount = 0
    finishResult = nil
    fadeResult = nil
    result(nil)
  }

  private func append(_ arguments: Any?, result: @escaping FlutterResult) {
    guard
      let values = arguments as? [String: Any],
      let turnId = validTurnId(values["turnId"]),
      turnId == activeTurnId,
      finishResult == nil,
      fadeResult == nil,
      let typedData = values["audio"] as? FlutterStandardTypedData,
      !typedData.data.isEmpty,
      typedData.data.count <= 2 * 1024 * 1024,
      typedData.data.count.isMultiple(of: 2),
      let format,
      let buffer = AVAudioPCMBuffer(
        pcmFormat: format,
        frameCapacity: AVAudioFrameCount(typedData.data.count / 2)
      ),
      let samples = buffer.int16ChannelData?[0]
    else {
      result(flutterError(
        "invalid_pcm_speech_chunk",
        "PCM speech received an invalid turn or incomplete PCM16 chunk."
      ))
      return
    }

    buffer.frameLength = buffer.frameCapacity
    typedData.data.withUnsafeBytes { source in
      guard let baseAddress = source.baseAddress else { return }
      memcpy(samples, baseAddress, typedData.data.count)
    }

    let scheduledGeneration = generation
    scheduledBufferCount += 1
    scheduledSampleCount += Int64(buffer.frameLength)
    player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) {
      [weak self] _ in
      DispatchQueue.main.async {
        guard
          let self,
          self.generation == scheduledGeneration,
          self.activeTurnId == turnId
        else { return }
        self.completedBufferCount += 1
        self.completeIfReady()
      }
    }
    result(nil)
  }

  private func finish(_ arguments: Any?, result: @escaping FlutterResult) {
    guard
      let values = arguments as? [String: Any],
      let turnId = validTurnId(values["turnId"]),
      turnId == activeTurnId,
      scheduledBufferCount > 0,
      finishResult == nil,
      fadeResult == nil
    else {
      result(flutterError(
        "invalid_pcm_speech_finish",
        "PCM speech cannot finish an unknown or empty turn."
      ))
      return
    }
    finishResult = result
    completeIfReady()
  }

  private func position(_ arguments: Any?, result: @escaping FlutterResult) {
    guard let values = arguments as? [String: Any],
          let turnId = validTurnId(values["turnId"]) else {
      result(flutterError(
        "invalid_pcm_speech_position",
        "PCM speech cannot read the position of an unknown turn."
      ))
      return
    }
    if turnId == activeTurnId {
      result(NSNumber(value: currentSamplePosition()))
      return
    }
    if turnId == completedTurnId {
      result(NSNumber(value: completedSampleCount))
      return
    }
    result(flutterError(
      "invalid_pcm_speech_position",
      "PCM speech cannot read the position of an unknown turn."
    ))
  }

  private func progress(_ arguments: Any?, result: @escaping FlutterResult) {
    guard let values = arguments as? [String: Any],
          let turnId = validTurnId(values["turnId"]) else {
      result(flutterError(
        "invalid_pcm_speech_progress",
        "PCM speech cannot read progress for an unknown turn."
      ))
      return
    }
    if turnId == activeTurnId {
      result([
        "renderedSampleCount": NSNumber(value: currentSamplePosition()),
        "scheduledSampleCount": NSNumber(value: scheduledSampleCount)
      ])
      return
    }
    if turnId == completedTurnId {
      result([
        "renderedSampleCount": NSNumber(value: completedSampleCount),
        "scheduledSampleCount": NSNumber(value: completedSampleCount)
      ])
      return
    }
    result(flutterError(
      "invalid_pcm_speech_progress",
      "PCM speech cannot read progress for an unknown turn."
    ))
  }

  private func fadeOutAndStop(
    _ arguments: Any?,
    result: @escaping FlutterResult
  ) {
    guard
      let values = arguments as? [String: Any],
      let turnId = validTurnId(values["turnId"]),
      turnId == activeTurnId,
      let fadeDuration = values["fadeDurationMs"] as? NSNumber,
      (0...200).contains(fadeDuration.intValue),
      fadeResult == nil
    else {
      result(flutterError(
        "invalid_pcm_speech_fade",
        "PCM speech fade-stop requires an active turn and 0-200 ms duration."
      ))
      return
    }

    let pendingFinish = finishResult
    finishResult = nil
    pendingFinish?(flutterError(
      "pcm_speech_cancelled",
      "Ava's PCM speech was interrupted."
    ))
    fadeResult = result
    let fadeGeneration = generation
    let initialVolume = player.volume
    let durationMs = fadeDuration.intValue
    let stepCount = durationMs == 0 ? 1 : 8

    for step in 1...stepCount {
      let delay = durationMs == 0
        ? 0
        : (Double(durationMs) / 1000.0) * (Double(step) / Double(stepCount))
      DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
        guard
          let self,
          self.generation == fadeGeneration,
          self.activeTurnId == turnId,
          self.fadeResult != nil
        else { return }
        self.player.volume = initialVolume * Float(stepCount - step) / Float(stepCount)
        guard step == stepCount else { return }
        let renderedSamples = self.currentSamplePosition()
        let completion = self.fadeResult
        self.fadeResult = nil
        self.reset()
        completion?([
          "renderedSampleCount": NSNumber(value: renderedSamples)
        ])
      }
    }
  }

  private func currentSamplePosition() -> Int64 {
    guard
      let nodeTime = player.lastRenderTime,
      nodeTime.isSampleTimeValid,
      let playerTime = player.playerTime(forNodeTime: nodeTime)
    else { return 0 }
    return min(
      scheduledSampleCount,
      max(Int64(0), playerTime.sampleTime)
    )
  }

  private func completeIfReady() {
    guard
      let completion = finishResult,
      completedBufferCount == scheduledBufferCount
    else { return }
    let renderedSamples = scheduledSampleCount
    completedTurnId = activeTurnId
    completedSampleCount = renderedSamples
    finishResult = nil
    reset()
    completion([
      "renderedSampleCount": NSNumber(value: renderedSamples)
    ])
  }

  private func stop() {
    let pendingFinish = finishResult
    let pendingFade = fadeResult
    finishResult = nil
    fadeResult = nil
    reset()
    pendingFinish?(flutterError(
      "pcm_speech_cancelled",
      "Ava's PCM speech was cancelled."
    ))
    pendingFade?(flutterError(
      "pcm_speech_cancelled",
      "Ava's PCM speech fade was cancelled."
    ))
  }

  private func reset() {
    generation = UUID()
    player.stop()
    audioEngine.stop()
    audioEngine.disconnectNodeOutput(player)
    format = nil
    activeTurnId = nil
    scheduledBufferCount = 0
    completedBufferCount = 0
    scheduledSampleCount = 0
    try? AVAudioSession.sharedInstance().setActive(
      false,
      options: .notifyOthersOnDeactivation
    )
  }

  private func validTurnId(_ value: Any?) -> String? {
    guard let value = value as? String else { return nil }
    let range = NSRange(value.startIndex..<value.endIndex, in: value)
    let expression = try? NSRegularExpression(
      pattern: "^[A-Za-z0-9._:-]{8,64}$"
    )
    return expression?.firstMatch(in: value, range: range) == nil ? nil : value
  }

  private func flutterError(_ code: String, _ message: String) -> FlutterError {
    FlutterError(code: code, message: message, details: nil)
  }
}

private final class SpeechRecognitionChannelHandler: NSObject, @unchecked Sendable {
  private let channel: FlutterMethodChannel
  private let audioEngine = AVAudioEngine()
  private var recognitionTask: SFSpeechRecognitionTask?
  private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
  private var activeResult: FlutterResult?
  private var bestTranscript = ""
  private var silenceTimer: Timer?
  private var maximumTimer: Timer?

  init(messenger: FlutterBinaryMessenger) {
    channel = FlutterMethodChannel(name: "ava/speech_input", binaryMessenger: messenger)
    super.init()
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self else {
        result(FlutterError(
          code: "speech_recognition_unavailable",
          message: "The iOS speech recognition handler is unavailable.",
          details: nil
        ))
        return
      }
      switch call.method {
      case "listen":
        self.listen(call.arguments, result: result)
      case "stop":
        self.cancelActiveRecognition()
        result(nil)
      default:
        result(FlutterMethodNotImplemented)
      }
    }
  }

  private func listen(_ arguments: Any?, result: @escaping FlutterResult) {
    guard activeResult == nil else {
      result(FlutterError(
        code: "speech_recognition_busy",
        message: "Ava is already listening.",
        details: nil
      ))
      return
    }
    guard
      let arguments = arguments as? [String: Any],
      let locale = arguments["locale"] as? String,
      !locale.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else {
      result(FlutterError(
        code: "invalid_speech_recognition_request",
        message: "Speech recognition requires a locale.",
        details: arguments
      ))
      return
    }

    authorizeSpeech { [weak self] speechError in
      guard let self else { return }
      if let speechError {
        result(speechError)
        return
      }
      self.authorizeMicrophone { [weak self] microphoneError in
        guard let self else { return }
        if let microphoneError {
          result(microphoneError)
          return
        }
        self.startRecognition(locale: locale, result: result)
      }
    }
  }

  private func authorizeSpeech(_ completion: @escaping (FlutterError?) -> Void) {
    func resolve(_ status: SFSpeechRecognizerAuthorizationStatus) {
      switch status {
      case .authorized:
        completion(nil)
      case .denied, .restricted:
        completion(FlutterError(
          code: "speech_recognition_permission_denied",
          message: "Speech recognition permission is required in Settings.",
          details: nil
        ))
      case .notDetermined:
        completion(FlutterError(
          code: "speech_recognition_permission_unresolved",
          message: "Speech recognition permission was not resolved.",
          details: nil
        ))
      @unknown default:
        completion(FlutterError(
          code: "speech_recognition_permission_unknown",
          message: "iOS returned an unknown speech recognition permission state.",
          details: nil
        ))
      }
    }

    let current = SFSpeechRecognizer.authorizationStatus()
    if current == .notDetermined {
      SFSpeechRecognizer.requestAuthorization { status in
        DispatchQueue.main.async { resolve(status) }
      }
    } else {
      resolve(current)
    }
  }

  private func authorizeMicrophone(_ completion: @escaping (FlutterError?) -> Void) {
    let session = AVAudioSession.sharedInstance()
    switch session.recordPermission {
    case .granted:
      completion(nil)
    case .undetermined:
      session.requestRecordPermission { granted in
        DispatchQueue.main.async {
          completion(granted ? nil : FlutterError(
            code: "microphone_permission_denied",
            message: "Microphone permission is required in Settings.",
            details: nil
          ))
        }
      }
    case .denied:
      completion(FlutterError(
        code: "microphone_permission_denied",
        message: "Microphone permission is required in Settings.",
        details: nil
      ))
    @unknown default:
      completion(FlutterError(
        code: "microphone_permission_unknown",
        message: "iOS returned an unknown microphone permission state.",
        details: nil
      ))
    }
  }

  private func startRecognition(locale: String, result: @escaping FlutterResult) {
    guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: locale)) else {
      result(FlutterError(
        code: "speech_recognizer_locale_unavailable",
        message: "No speech recognizer is available for \(locale).",
        details: nil
      ))
      return
    }
    guard recognizer.isAvailable else {
      result(FlutterError(
        code: "speech_recognizer_offline",
        message: "Apple speech recognition is currently unavailable.",
        details: nil
      ))
      return
    }

    do {
      let audioSession = AVAudioSession.sharedInstance()
      try audioSession.setCategory(.record, mode: .measurement, options: [.duckOthers])
      try audioSession.setActive(true, options: .notifyOthersOnDeactivation)
    } catch {
      result(FlutterError(
        code: "microphone_audio_session_failed",
        message: "Could not prepare microphone input: \(error.localizedDescription)",
        details: nil
      ))
      return
    }

    let request = SFSpeechAudioBufferRecognitionRequest()
    request.shouldReportPartialResults = true
    request.taskHint = .dictation
    let inputNode = audioEngine.inputNode
    let format = inputNode.outputFormat(forBus: 0)
    guard format.sampleRate > 0 && format.channelCount > 0 else {
      result(FlutterError(
        code: "microphone_format_unavailable",
        message: "The microphone did not provide a usable audio format.",
        details: nil
      ))
      return
    }

    activeResult = result
    bestTranscript = ""
    recognitionRequest = request
    inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
      request.append(buffer)
    }
    audioEngine.prepare()
    do {
      try audioEngine.start()
    } catch {
      cleanupRecognition()
      result(FlutterError(
        code: "microphone_start_failed",
        message: "Could not start microphone capture: \(error.localizedDescription)",
        details: nil
      ))
      return
    }

    recognitionTask = recognizer.recognitionTask(with: request) { [weak self] recognitionResult, error in
      DispatchQueue.main.async {
        guard let self, self.activeResult != nil else { return }
        if let recognitionResult {
          let transcript = recognitionResult.bestTranscription.formattedString
            .trimmingCharacters(in: .whitespacesAndNewlines)
          if !transcript.isEmpty {
            self.bestTranscript = transcript
            self.scheduleSilenceCompletion()
          }
          if recognitionResult.isFinal {
            self.completeRecognition()
            return
          }
        }
        if let error {
          self.failRecognition(
            code: "speech_recognition_failed",
            message: "Speech recognition failed: \(error.localizedDescription)"
          )
        }
      }
    }
    maximumTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: false) { [weak self] _ in
      self?.completeRecognition()
    }
  }

  private func scheduleSilenceCompletion() {
    silenceTimer?.invalidate()
    silenceTimer = Timer.scheduledTimer(withTimeInterval: 1.25, repeats: false) { [weak self] _ in
      self?.completeRecognition()
    }
  }

  private func completeRecognition() {
    guard activeResult != nil else { return }
    let transcript = bestTranscript
    if transcript.isEmpty {
      failRecognition(
        code: "speech_recognition_empty",
        message: "Ava did not hear any speech."
      )
      return
    }
    let result = activeResult
    cleanupRecognition()
    result?(transcript)
  }

  private func failRecognition(code: String, message: String) {
    let result = activeResult
    cleanupRecognition()
    result?(FlutterError(code: code, message: message, details: nil))
  }

  private func cancelActiveRecognition() {
    guard activeResult != nil else { return }
    let result = activeResult
    cleanupRecognition()
    result?(FlutterError(
      code: "speech_recognition_cancelled",
      message: "Ava stopped listening.",
      details: nil
    ))
  }

  private func cleanupRecognition() {
    silenceTimer?.invalidate()
    maximumTimer?.invalidate()
    silenceTimer = nil
    maximumTimer = nil
    if audioEngine.isRunning { audioEngine.stop() }
    audioEngine.inputNode.removeTap(onBus: 0)
    recognitionRequest?.endAudio()
    recognitionTask?.cancel()
    recognitionTask = nil
    recognitionRequest = nil
    activeResult = nil
    bestTranscript = ""
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }
}

private final class SpeechChannelHandler: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
  private let synthesizer = AVSpeechSynthesizer()
  private let channel: FlutterMethodChannel
  private var activeUtterance: AVSpeechUtterance?
  private var activeResult: FlutterResult?

  init(messenger: FlutterBinaryMessenger) {
    channel = FlutterMethodChannel(name: "ava/speech", binaryMessenger: messenger)
    super.init()
    synthesizer.delegate = self
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self else {
        result(FlutterError(
          code: "speech_unavailable",
          message: "The iOS speech handler is unavailable.",
          details: nil
        ))
        return
      }
      self.handle(call, result: result)
    }
  }

  private func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "speak":
      speak(call.arguments, result: result)
    case "stop":
      synthesizer.stopSpeaking(at: .immediate)
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func speak(_ arguments: Any?, result: @escaping FlutterResult) {
    guard activeUtterance == nil else {
      result(FlutterError(
        code: "speech_busy",
        message: "Ava is already speaking.",
        details: nil
      ))
      return
    }
    guard
      let arguments = arguments as? [String: Any],
      let text = arguments["text"] as? String,
      !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
      let volumeNumber = arguments["volume"] as? NSNumber,
      let language = arguments["language"] as? String
    else {
      result(FlutterError(
        code: "invalid_speech_request",
        message: "Speech requires non-empty text, a volume, and a language.",
        details: arguments
      ))
      return
    }
    guard let voice = AVSpeechSynthesisVoice(language: language) else {
      result(FlutterError(
        code: "speech_voice_unavailable",
        message: "No system speech voice is installed for \(language).",
        details: nil
      ))
      return
    }

    do {
      let audioSession = AVAudioSession.sharedInstance()
      try audioSession.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
      try audioSession.setActive(true)
    } catch {
      result(FlutterError(
        code: "speech_audio_session_failed",
        message: "Could not prepare the iOS audio session: \(error.localizedDescription)",
        details: nil
      ))
      return
    }

    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = voice
    utterance.volume = Float(truncating: volumeNumber)
    utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.92
    utterance.pitchMultiplier = 1.04
    activeUtterance = utterance
    activeResult = result
    synthesizer.speak(utterance)
  }

  func speechSynthesizer(
    _ synthesizer: AVSpeechSynthesizer,
    didFinish utterance: AVSpeechUtterance
  ) {
    complete(utterance, value: nil)
  }

  func speechSynthesizer(
    _ synthesizer: AVSpeechSynthesizer,
    didCancel utterance: AVSpeechUtterance
  ) {
    complete(
      utterance,
      value: FlutterError(
        code: "speech_cancelled",
        message: "Ava's speech was cancelled.",
        details: nil
      )
    )
  }

  private func complete(_ utterance: AVSpeechUtterance, value: Any?) {
    guard utterance === activeUtterance else { return }
    let result = activeResult
    activeUtterance = nil
    activeResult = nil
    result?(value)
  }
}
