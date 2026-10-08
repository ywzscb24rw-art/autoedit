import { useEffect, useState } from 'react'
import { api, desktop, openLink, type Settings } from './api'

const KEYS_URL = 'https://platform.claude.com/settings/keys'
const BILLING_URL = 'https://platform.claude.com/settings/billing'

function KeyForm({ settings, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      onSaved(await api.saveSettings({ api_key: key }))
      setKey('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      {settings.has_key && <p className="muted">Current key: <code>{settings.key_hint}</code></p>}
      <div className="row">
        <input type="password" placeholder="sk-ant-..." value={key} onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && key && save()} autoFocus={!settings.has_key} />
        <button className="primary" onClick={save} disabled={!key || busy}>{busy ? 'Checking…' : 'Save key'}</button>
      </div>
      {error && <p className="error">{error}</p>}
      <p className="muted">
        Create one at <a href="#" onClick={(e) => { e.preventDefault(); openLink(KEYS_URL) }}>platform.claude.com</a> and
        add a few dollars of credit under <a href="#" onClick={(e) => { e.preventDefault(); openLink(BILLING_URL) }}>Billing</a>.
        A typical video costs a few cents. Your key stays on this Mac.
      </p>
    </div>
  )
}

function ModelDownload({ settings, refresh }: { settings: Settings; refresh: () => void }) {
  const sm = settings.speech_model
  useEffect(() => {
    if (sm.status !== 'downloading') return
    const t = setInterval(refresh, 1000)
    return () => clearInterval(t)
  }, [sm.status, refresh])

  if (sm.status === 'ready') return <p className="ok">✓ Speech model ready</p>
  const gb = sm.total_bytes ? (sm.total_bytes / 1e9).toFixed(1) : '1.6'
  return (
    <div className="stack">
      {sm.status === 'downloading' ? (
        <div className="progress">
          <div className="bar" style={{ width: `${Math.round(sm.progress * 100)}%` }} />
          <span>Downloading… {Math.round(sm.progress * 100)}% of {gb} GB</span>
        </div>
      ) : (
        <button className="primary" onClick={async () => { await api.downloadModel(); refresh() }}>
          Download speech model ({gb} GB)
        </button>
      )}
      {sm.error && <p className="error">{sm.error}</p>}
      <p className="muted">AutoEdit transcribes on your Mac, so your videos never leave it. This is a one-time download.</p>
    </div>
  )
}

export function SettingsPanel({ settings, setSettings, refresh }: {
  settings: Settings; setSettings: (s: Settings) => void; refresh: () => void
}) {
  return (
    <>
      <section className="card">
        <h2>Claude API key</h2>
        <KeyForm settings={settings} onSaved={setSettings} />
      </section>
      <section className="card">
        <h2>Speech model</h2>
        <ModelDownload settings={settings} refresh={refresh} />
      </section>
      <section className="card">
        <h2>Editing model</h2>
        <select value={settings.model} onChange={async (e) => setSettings(await api.saveSettings({ model: e.target.value }))}>
          {Object.entries(settings.models).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </section>
      {desktop && (
        <section className="card">
          <h2>Your projects</h2>
          <p className="muted">Recordings and edits are stored in <code>{settings.data_dir}</code>.</p>
          <button onClick={() => desktop?.showInFolder(settings.data_dir)}>Show in Finder</button>
          <p className="muted">AutoEdit beta {desktop.version}</p>
        </section>
      )}
    </>
  )
}

export default function SetupPage({ settings, setSettings, refresh, onDone }: {
  settings: Settings; setSettings: (s: Settings) => void; refresh: () => void; onDone: () => void
}) {
  const keyDone = settings.has_key
  const modelDone = settings.speech_model.status === 'ready'
  return (
    <main className="home">
      <section className="card">
        <h2>Welcome to AutoEdit</h2>
        <p className="muted">Two quick steps and you're ready to record.</p>
      </section>
      <section className={`card step ${keyDone ? 'done' : ''}`}>
        <h2>1. Add your Claude API key {keyDone && '✓'}</h2>
        <p className="muted">Claude reads your transcript and makes the editing decisions.</p>
        <KeyForm settings={settings} onSaved={setSettings} />
      </section>
      <section className={`card step ${modelDone ? 'done' : ''}`}>
        <h2>2. Download the speech model {modelDone && '✓'}</h2>
        <ModelDownload settings={settings} refresh={refresh} />
      </section>
      <section className="card">
        <h2>When you first record</h2>
        <p className="muted">
          macOS will ask to let AutoEdit record your screen and use your microphone. Allow both. If you clicked
          Don't Allow, turn AutoEdit on in System Settings → Privacy &amp; Security → Screen &amp; System Audio Recording,
          then reopen the app.
        </p>
        <button className="primary" disabled={!keyDone || !modelDone} onClick={onDone}>Start using AutoEdit</button>
      </section>
    </main>
  )
}
