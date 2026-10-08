import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, CONTENT_LABELS, effectiveStyle, fmt, media, type Content, type ProjectData, type Style } from './api'

const STAGES: Record<string, string> = {
  queued: 'Queued', ingest: 'Preparing video (and a fast preview copy for 4K)', transcribe: 'Transcribing',
  clean: 'Finding fillers and silence', 'ai-edit': 'Claude is editing', hooks: 'Claude is sharpening clip openings', scenes: 'Finding scene cuts for B-roll', broll: 'Claude is reviewing B-roll', proxy: 'Making a fast preview copy (one time)', render: 'Rendering preview', export: 'Exporting full resolution',
}
const MAX_GAP = 0.6 // must match EdlParams.max_gap

export default function ProjectPage({ id }: { id: string }) {
  const [data, setData] = useState<ProjectData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [view, setView] = useState<string>('') // output name, or 'source'
  const [renderedAt, setRenderedAt] = useState(Date.now())
  const player = useRef<HTMLVideoElement>(null)

  const load = useCallback(() => api.get(id).then(setData).catch((e) => setError(String(e))), [id])
  useEffect(() => { load() }, [load])

  const running = data?.state.status === 'running'
  useEffect(() => {
    if (!running) return
    const t = setInterval(async () => {
      const d = await api.get(id)
      setData(d)
      if (d.state.status !== 'running') { setDirty(false); setRenderedAt(Date.now()) }
    }, 1000)
    return () => clearInterval(t)
  }, [running, id])

  const outputs = data?.state.outputs ?? []
  const current = view === 'source' ? null : outputs.find((o) => o.name === view) ?? outputs[0]
  const clipIndex = current?.name.startsWith('clip_') ? Number(current.name.slice(5)) : null
  const clip = clipIndex !== null ? data?.edits?.clips[clipIndex] : undefined
  const clipSegs = useMemo(() => new Set(clip?.segment_ids ?? []), [clip])

  if (error) return <main className="project"><p className="error">{error}</p></main>
  if (!data) return <main className="project"><p className="muted">Loading…</p></main>
  const { state, transcript, edits, cut } = data

  async function toggle(i: number) {
    if (!edits) return
    const isCut = i in cut
    const auto = String(i) in edits.auto_cuts || (transcript && String(transcript.words[i].seg) in edits.segment_cuts)
    // Clicking a word flips it. If that matches the automatic decision, drop the override.
    const want = isCut // want it kept
    const override = auto ? (want ? true : null) : (want ? null : false)
    setData(await api.overrides(id, { [i]: override }))
    setDirty(true)
  }

  async function rerun(content?: Content) {
    if (!edits) return
    // Switching the video type resets clip lengths to that type's defaults.
    const lengths = content && content !== edits.opts.content ? { min_s: null, max_s: null } : {}
    await api.process(id, { ...edits.opts, ...lengths, content: content ?? edits.opts.content, mode: edits.mode, use_ai: edits.use_ai })
    load()
  }
  async function render() { await api.render(id); load() }
  async function setStyle(style: Style) { setData(await api.style(id, style)); setDirty(true) }
  async function exportFull(name: string) { await api.export(id, name); load() }

  function seek(t: number) {
    setView('source')
    requestAnimationFrame(() => {
      if (player.current) { player.current.currentTime = t; player.current.play() }
    })
  }

  const src = current ? media(id, current.file, renderedAt) : data.has_source ? media(id, 'source.mp4') : null
  const original = state.media?.duration
  // 16:9 previews render at 720p (PREVIEW_HEIGHT in render.py); full resolution is an explicit export.
  const fullHeight = state.media?.height ?? 0
  const canExport = !!current && fullHeight > 720 && !(clip && edits?.opts.vertical)
  const fullLabel = fullHeight >= 2160 ? '4K' : `${fullHeight}p`

  return (
    <main className="project">
      <div className="head">
        <h2>{state.name}</h2>
        <div className="actions">
          {edits && (
            <select value={edits.opts.content ?? 'screen'} disabled={running} title="Video type"
              onChange={(e) => rerun(e.target.value as Content)}>
              {Object.entries(CONTENT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          )}
          {edits && (() => {
            const st = effectiveStyle({ ...edits.opts, mode: edits.mode, use_ai: edits.use_ai }, !!state.burned_captions)
            return <>
              <label className="check"><input type="checkbox" checked={st.captions} disabled={running}
                onChange={(e) => setStyle({ captions: e.target.checked })} /> Captions
                {state.burned_captions && edits.opts.captions == null &&
                  <span className="muted" title="We found captions already burned into this video"> (has its own)</span>}</label>
              <label className="check"><input type="checkbox" checked={st.punch_in} disabled={running}
                onChange={(e) => setStyle({ punch_in: e.target.checked })} /> Punch-ins</label>
            </>
          })()}
          {edits && <button onClick={() => rerun()} disabled={running}>{edits.use_ai ? 'Re-run AI edit' : 'Re-run cleanup'}</button>}
          {edits && <button className={dirty ? 'primary' : ''} onClick={render} disabled={running}>Render{dirty ? ' changes' : ''}</button>}
        </div>
      </div>

      {running && <Progress state={state} />}
      {state.status === 'error' && <p className="error">{state.error}</p>}

      <div className="layout">
        <section className="left">
          <div className="tabs">
            {outputs.map((o) => (
              <button key={o.name} className={current?.name === o.name ? 'on' : ''} onClick={() => setView(o.name)}>
                {o.name === 'main' ? 'Edited' : edits?.clips[Number(o.name.slice(5))]?.title ?? o.name}
              </button>
            ))}
            {data.has_source && <button className={view === 'source' || !current ? 'on' : ''} onClick={() => setView('source')}>Original</button>}
          </div>
          {src && <video ref={player} key={src} src={src} controls className={clip && edits?.opts.vertical ? 'vertical' : ''} />}
          {current && original && (
            <p className="muted stats">
              {fmt(original)} → <strong>{fmt(current.duration)}</strong>
              {current.name === 'main' && <> ({Math.round((1 - current.duration / original) * 100)}% shorter, {current.cuts} pieces)</>}
              {' · '}<a href={media(id, current.file, renderedAt)} download>Download{canExport ? ' 720p preview' : ''}</a>
              {canExport && (current.export_file
                ? <>{' · '}<a href={media(id, current.export_file, renderedAt)} download>Download {fullLabel}</a></>
                : <>{' · '}<button className="link" onClick={() => exportFull(current.name)} disabled={running}>Export {fullLabel}</button></>)}
            </p>
          )}

          {clip && (
            <div className="card clip">
              <div className="row"><strong>{clip.title}</strong><span className="badge">{clip.score}/10</span></div>
              <p><em>“{clip.hook}”</em></p>
              <p className="muted">{clip.reason}</p>
            </div>
          )}
          {edits?.summary && !clip && (
            <div className="card">
              <h3>Claude's edit notes</h3>
              <p>{edits.summary}</p>
              {Object.keys(edits.segment_cuts).length > 0 && (
                <ul className="cut-reasons">
                  {Object.entries(groupReasons(edits.segment_cuts)).map(([reason, ids]) => (
                    <li key={reason}><span className="chip ai">{ids.length}</span> {reason}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="legend">
            <span className="w filler">filler</span><span className="w stutter">stutter</span>
            <span className="w ai">cut by AI</span><span className="w manual">cut by you</span>
            <span className="muted">Click a word to cut or restore it. Click a timestamp to play the original.</span>
          </div>
        </section>

        <section className="right transcript">
          {!transcript && <p className="muted">The transcript will appear here.</p>}
          {transcript?.segments.map((s) => {
            const [a, b] = s.words
            const inClip = clip ? clipSegs.has(s.id) : true
            const prev = a > 0 ? transcript.words[a - 1] : null
            const gap = prev ? transcript.words[a].start - prev.end : 0
            return (
              <p key={s.id} className={`seg ${inClip ? '' : 'dim'}`}>
                {gap > MAX_GAP && <span className="gap">⏸ {gap.toFixed(1)}s</span>}
                <button className="ts" onClick={() => seek(s.start)}>{fmt(s.start)}</button>
                {transcript.words.slice(a, b).map((w, k) => {
                  const i = a + k
                  const reason = cut[i]
                  const g = k > 0 ? w.start - transcript.words[i - 1].end : 0
                  return (
                    <span key={i}>
                      {g > MAX_GAP && <span className="gap">⏸ {g.toFixed(1)}s</span>}
                      <span className={`w ${reason ?? ''}`} onClick={() => toggle(i)}
                        title={`${fmt(w.start)}${reason ? ` · ${reason}` : ''}`}>{w.w}</span>
                    </span>
                  )
                })}
              </p>
            )
          })}
        </section>
      </div>
    </main>
  )
}

function groupReasons(cuts: Record<string, string>) {
  const out: Record<string, string[]> = {}
  for (const [id, r] of Object.entries(cuts)) (out[r] ??= []).push(id)
  return out
}

function Progress({ state }: { state: ProjectData['state'] }) {
  const label = STAGES[state.stage ?? ''] ?? state.stage
  const video = state.video_progress
  // While transcribing, the video converts in parallel; show both.
  const side = state.stage === 'transcribe' && video !== undefined && video < 1
    ? ` · converting video ${Math.round(video * 100)}%` : ''
  const pct = state.stage === 'ingest' && state.progress === null && video !== undefined ? video : state.progress
  return (
    <div className={`progress ${pct === null ? 'indeterminate' : ''}`}>
      <div className="bar" style={{ width: pct === null ? '100%' : `${Math.round(pct * 100)}%` }} />
      <span>{label}…{pct !== null && ` ${Math.round(pct * 100)}%`}{side}</span>
    </div>
  )
}
