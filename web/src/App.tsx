import { useEffect, useState } from 'react'
import HomePage from './HomePage'
import ProjectPage from './ProjectPage'

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
  const projectId = hash.match(/^#\/p\/(.+)$/)?.[1]
  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">AutoEdit</a>
        <span className="tagline">record → transcribe → edit</span>
      </header>
      {projectId ? <ProjectPage key={projectId} id={projectId} /> : <HomePage />}
    </div>
  )
}
