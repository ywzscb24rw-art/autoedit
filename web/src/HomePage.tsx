import { useEffect, useRef, useState } from 'react'
import { api, fmt, type Mode, type State } from './api'
import { Recorder } from './recorder'

export default function HomePage() {
  const [mode, setMode] = useState<Mode>('clean')
  const [mic, setMic] = useState(true)
  const [systemAudio, setSystemAudio] = useState(false)
  const [vertical, setVertical] = useState(true)
  const [useAi, setUseAi] = useState(true)
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [projects, setProjects] = useState<State[]>([])
  const recorder = useRef<Recorder | null>(null)
  const preview = useRef<HTMLVideoElement>(null)

  useEffect(() => { api.list().then(setProjects).catch(() => {}) }, [])
  useEffect(() => {
    if (!recording) return
    const t0 = Date.now()
    const t = setInterval(() => setElapsed((Date.now() - t0) / 1000), 200)
    return () => clearInterval(t)
  }, [recording])

  async function submit(file: Blob, name: string) {
    setBusy('Uploading…')
    try {
      const st = await api.upload(file, name)
      await api.process(st.id, { mode, use_ai: mode === 'clips' || useAi, vertical })
      location.hash = `#/p/${st.id}`
    } catch (e) {
      setError(String(e))
      setBusy(null)
    }
  }

  async function start() {
    setError(null)
    const r = new Recorder()
    try {
      const display = await r.start({ mic, systemAudio }, () => stop())
      recorder.current = r
      if (preview.current) preview.current.srcObject = display
      setElapsed(0)
      setRecording(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function stop() {
    const r = recorder.current
    if (!r) return
    recorder.current = null
    setRecording(false)
    if (preview.current) preview.current.srcObject = null
    const blob = await r.stop()
    await submit(blob, `Recording ${new Date().toLocaleString()}`)
  }

  return (
    <main className="home">
      <section className="card recorder">
        <h2>New recording</h2>

        <div className="mode-picker">
          <button className={mode === 'clean' ? 'on' : ''} onClick={() => setMode('clean')} disabled={recording}>
            <strong>Clean edit</strong>
            <span>For courses and tutorials: cut fillers, dead air, retakes, and tangents into one tight video</span>
          </button>
          <button className={mode === 'clips' ? 'on' : ''} onClick={() => setMode('clips')} disabled={recording}>
            <strong>Find clips</strong>
            <span>For clippers: pull the best 30–90s standalone moments, each with a hook and payoff</span>
          </button>
        </div>

        <div className="toggles">
          <label><input type="checkbox" checked={mic} onChange={(e) => setMic(e.target.checked)} disabled={recording} /> Microphone</label>
          <label><input type="checkbox" checked={systemAudio} onChange={(e) => setSystemAudio(e.target.checked)} disabled={recording} /> Tab / system audio</label>
          {mode === 'clean' && (
            <label><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> AI narrative edit (Claude)</label>
          )}
          {mode === 'clips' && (
            <label><input type="checkbox" checked={vertical} onChange={(e) => setVertical(e.target.checked)} /> Vertical 9:16 crop</label>
          )}
        </div>

        <video ref={preview} className={`live ${recording ? '' : 'hidden'}`} autoPlay muted playsInline />

        <div className="actions">
          {recording ? (
            <button className="primary rec" onClick={stop}><span className="dot" /> Stop · {fmt(elapsed)}</button>
          ) : (
            <button className="primary" onClick={start} disabled={!!busy}>● Start recording</button>
          )}
          <label className={`button ${busy || recording ? 'disabled' : ''}`}>
            Upload a video
            <input type="file" accept="video/*" hidden disabled={!!busy || recording}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) submit(f, f.name) }} />
          </label>
          {busy && <span className="muted">{busy}</span>}
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <h2>Projects</h2>
        {projects.length === 0 && <p className="muted">Nothing yet. Record something above.</p>}
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <a href={`#/p/${p.id}`}>{p.name}</a>
              <span className={`badge ${p.status}`}>{p.status}</span>
              {p.media && <span className="muted">{fmt(p.media.duration)}</span>}
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
