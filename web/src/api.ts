export type Mode = 'clean' | 'clips'

export interface Settings {
  has_key: boolean; key_hint: string | null; model: string; models: Record<string, string>
  data_dir: string
  speech_model: { status: 'idle' | 'downloading' | 'ready' | 'error'; progress: number; error: string | null; total_bytes: number | null }
}

// Bridge from the desktop shell (desktop/preload.js); absent when running in a plain browser.
interface DesktopBridge {
  version: string
  showInFolder: (path: string) => void
  openExternal: (url: string) => void
  sendFeedback: () => void
}
declare global { interface Window { autoedit?: DesktopBridge } }
export const desktop = window.autoedit
export const openLink = (url: string) => (desktop ? desktop.openExternal(url) : window.open(url, '_blank'))
export type Content = 'screen' | 'talking' | 'vlog'
export const CONTENT_LABELS: Record<Content, string> = {
  screen: 'Screen recording', talking: 'Talking head', vlog: 'Vlog (keeps B-roll)',
}
// Must match STYLE in server/pipeline/run.py.
export const STYLE_DEFAULTS: Record<Content, { punch_in: boolean; captions: boolean }> = {
  screen: { punch_in: false, captions: false },
  talking: { punch_in: true, captions: true },
  vlog: { punch_in: false, captions: false },
}
export type Style = { punch_in?: boolean | null; captions?: boolean | null }
// Mirrors render_style in server/pipeline/run.py: footage with its own captions defaults to none.
export const effectiveStyle = (opts: ProcessOpts, burnedCaptions = false) => {
  const d = STYLE_DEFAULTS[opts.content ?? 'screen']
  return { punch_in: opts.punch_in ?? d.punch_in, captions: opts.captions ?? (d.captions && !burnedCaptions) }
}

export interface Word { i: number; w: string; start: number; end: number; prob: number; seg: number }
export interface Segment { id: number; start: number; end: number; words: [number, number]; text: string }
export interface Transcript { language: string; duration: number; words: Word[]; segments: Segment[] }

export interface Clip {
  title: string; hook: string; segment_ids: number[]; reason: string; score: number; approx_duration: number
}
export interface Edits {
  mode: Mode; use_ai: boolean; opts: ProcessOpts
  auto_cuts: Record<string, string>; segment_cuts: Record<string, string>
  order: number[]; clips: Clip[]; summary: string; overrides: Record<string, boolean>
}
export interface Output { name: string; file: string; duration: number; cuts: number; export_file?: string }
export interface State {
  id: string; name: string; created: number
  status: 'new' | 'running' | 'done' | 'error'
  stage: string | null; progress: number | null; error: string | null
  video_progress?: number
  burned_captions?: boolean | null
  duplicate?: boolean
  media?: { duration: number; width: number; height: number; fps: string }
  outputs?: Output[]
}
export interface ProjectData {
  state: State; transcript: Transcript | null; edits: Edits | null
  cut: Record<string, string>; has_source: boolean
}
export interface ProcessOpts {
  mode: Mode; use_ai: boolean; vertical?: boolean; content?: Content
  punch_in?: boolean | null; captions?: boolean | null
  min_s?: number | null; max_s?: number | null; max_clips?: number
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText)
  return res.json()
}

export const api = {
  settings: () => fetch('/api/settings').then(json<Settings>),
  saveSettings: (body: { api_key?: string; model?: string }) =>
    fetch('/api/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(json<Settings>),
  downloadModel: () => fetch('/api/setup/model', { method: 'POST' }).then(json<unknown>),
  remove: (id: string) => fetch(`/api/projects/${id}`, { method: 'DELETE' }).then(json<unknown>),
  list: () => fetch('/api/projects').then(json<State[]>),
  get: (id: string) => fetch(`/api/projects/${id}`).then(json<ProjectData>),
  upload: (file: Blob, name: string) => {
    const fd = new FormData()
    const ext = file.type.includes('mp4') ? 'mp4' : file instanceof File ? file.name.split('.').pop() : 'webm'
    fd.append('file', file, `recording.${ext}`)
    fd.append('name', name)
    return fetch('/api/projects', { method: 'POST', body: fd }).then(json<State>)
  },
  process: (id: string, opts: ProcessOpts) =>
    fetch(`/api/projects/${id}/process`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts),
    }).then(json<State>),
  overrides: (id: string, overrides: Record<string, boolean | null>) =>
    fetch(`/api/projects/${id}/overrides`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ overrides }),
    }).then(json<ProjectData>),
  style: (id: string, style: Style) =>
    fetch(`/api/projects/${id}/style`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(style),
    }).then(json<ProjectData>),
  render: (id: string) => fetch(`/api/projects/${id}/render`, { method: 'POST' }).then(json<State>),
  export: (id: string, name: string) =>
    fetch(`/api/projects/${id}/export/${name}`, { method: 'POST' }).then(json<State>),
}

export const media = (id: string, file: string, bust?: number) =>
  `/media/${id}/${file}${bust ? `?v=${bust}` : ''}`

export const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`
