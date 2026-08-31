import Flutter
import UIKit
@preconcurrency import AVFoundation

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private var pcmSpeechChannelHandler: PcmSpeechChannelHandler?
  private var audioDiagnosticsChannel: FlutterMethodChannel?
  private var deviceContextChannel: FlutterMethodChannel?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let messenger = engineBridge.applicationRegistrar.messenger()
    pcmSpeechChannelHandler = PcmSpeechChannelHandler(
      messenger: messenger
    )
    audioDiagnosticsChannel = FlutterMethodChannel(
      name: "ava/audio_diagnostics",
      binaryMessenger: messenger
    )
    audioDiagnosticsChannel?.setMethodCallHandler { call, result in
      guard call.method == "currentRoute" else {
        result(FlutterMethodNotImplemented)
        return
      }
      let session = AVAudioSession.sharedInstance()
      result([
        "inputRouteType": session.currentRoute.inputs
          .map { $0.portType.rawValue }
          .joined(separator: ","),
        "outputRouteType": session.currentRoute.outputs
          .map { $0.portType.rawValue }
          .joined(separator: ","),
        "audioCategory": session.category.rawValue,
        "audioMode": session.mode.rawValue,
        "inputAvailable": session.isInputAvailable,
        "sampleRateHz": session.sampleRate,
      ])
    }
    deviceContextChannel = FlutterMethodChannel(
      name: "ava/device_context",
      binaryMessenger: messenger
    )
    deviceContextChannel?.setMethodCallHandler { call, result in
      guard call.method == "currentTimezoneId" else {
        result(FlutterMethodNotImplemented)
        return
      }
      result(TimeZone.current.identifier)
    }
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
      // The persistent WebRTC call owns AVAudioSession category, mode, route,
      // and active lifetime. Reconfiguring it as playback-only here disables
      // the capture-side voice-processing path while Ava speaks.
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
