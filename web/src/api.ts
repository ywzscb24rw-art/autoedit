export type Mode = 'clean' | 'clips'

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
export interface Output { name: string; file: string; duration: number; cuts: number }
export interface State {
  id: string; name: string; created: number
  status: 'new' | 'running' | 'done' | 'error'
  stage: string | null; progress: number; error: string | null
  media?: { duration: number; width: number; height: number }
  outputs?: Output[]
}
export interface ProjectData {
  state: State; transcript: Transcript | null; edits: Edits | null
  cut: Record<string, string>; has_source: boolean
}
export interface ProcessOpts {
  mode: Mode; use_ai: boolean; vertical?: boolean; min_s?: number; max_s?: number; max_clips?: number
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText)
  return res.json()
}

export const api = {
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
  render: (id: string) => fetch(`/api/projects/${id}/render`, { method: 'POST' }).then(json<State>),
}

export const media = (id: string, file: string, bust?: number) =>
  `/media/${id}/${file}${bust ? `?v=${bust}` : ''}`

export const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`
