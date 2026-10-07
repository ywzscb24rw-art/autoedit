/**
 * Screen recorder: captures the screen with getDisplayMedia, plus the microphone and
 * optionally the tab/system audio, mixed into one track through an AudioContext.
 */
export interface RecordOpts { mic: boolean; systemAudio: boolean }

const MIME_TYPES = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4']

export class Recorder {
  private rec?: MediaRecorder
  private chunks: Blob[] = []
  private streams: MediaStream[] = []
  private ctx?: AudioContext

  async start(opts: RecordOpts, onEnded: () => void): Promise<MediaStream> {
    const display = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 30 },
      audio: opts.systemAudio,
    })
    this.streams.push(display)

    const ctx = (this.ctx = new AudioContext())
    const mix = ctx.createMediaStreamDestination()
    let hasAudio = false
    if (display.getAudioTracks().length) {
      ctx.createMediaStreamSource(display).connect(mix)
      hasAudio = true
    }
    if (opts.mic) {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      this.streams.push(mic)
      ctx.createMediaStreamSource(mic).connect(mix)
      hasAudio = true
    }
    if (!hasAudio) {
      this.cleanup()
      throw new Error('No audio source. Turn on the microphone or share tab audio; the editor needs speech.')
    }

    const stream = new MediaStream([...display.getVideoTracks(), ...mix.stream.getAudioTracks()])
    const mimeType = MIME_TYPES.find((t) => MediaRecorder.isTypeSupported(t))
    this.rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6_000_000 })
    this.chunks = []
    this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data)
    this.rec.start(1000)
    // The user can also stop sharing from the browser's own "Stop sharing" bar.
    display.getVideoTracks()[0].addEventListener('ended', onEnded)
    return display
  }

  stop(): Promise<Blob> {
    return new Promise((resolve) => {
      const rec = this.rec
      if (!rec || rec.state === 'inactive') return resolve(new Blob(this.chunks))
      rec.onstop = () => {
        resolve(new Blob(this.chunks, { type: rec.mimeType }))
        this.cleanup()
      }
      rec.stop()
    })
  }

  private cleanup() {
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()))
    this.streams = []
    this.ctx?.close()
  }
}
