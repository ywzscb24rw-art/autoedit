import { useCallback, useEffect, useState } from 'react'
import { api, desktop, type Settings } from './api'
import HomePage from './HomePage'
import ProjectPage from './ProjectPage'
import SetupPage, { SettingsPanel } from './SetupPage'

function useHash() {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const on = () => setHash(location.hash)
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  return hash
}

export default function App() {
  const hash = useHash()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [setupDone, setSetupDone] = useState(false)
  const refresh = useCallback(() => { api.settings().then(setSettings).catch(() => {}) }, [])
  useEffect(refresh, [refresh])

  const projectId = hash.match(/^#\/p\/(.+)$/)?.[1]
  const ready = settings && settings.has_key && settings.speech_model.status === 'ready'

  let page
  if (!settings) page = <main className="home"><p className="muted">Starting…</p></main>
  else if (!ready && !setupDone) {
    page = <SetupPage settings={settings} setSettings={setSettings} refresh={refresh} onDone={() => setSetupDone(true)} />
  } else if (hash === '#/settings') {
    page = <main className="home"><SettingsPanel settings={settings} setSettings={setSettings} refresh={refresh} /></main>
  } else if (projectId) page = <ProjectPage key={projectId} id={projectId} dataDir={settings.data_dir} />
  else page = <HomePage />

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">AutoEdit</a>
        <span className="tagline">record → transcribe → edit{desktop && <> · beta {desktop.version}</>}</span>
        <nav className="nav">
          {desktop && <button className="link" onClick={desktop.sendFeedback}>Send feedback</button>}
          <a href="#/settings">Settings</a>
        </nav>
      </header>
      {page}
    </div>
  )
}
