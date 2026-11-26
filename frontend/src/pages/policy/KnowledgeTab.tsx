import { useState, type FormEvent, type ReactNode } from 'react'
import { ExternalLink, Loader2, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Banner } from '@/components/Labels'
import { api, ApiError } from '@/lib/api'
import { useApi } from '@/lib/useApi'
import { dateIN } from '@/lib/format'
import type { KbHit, KbSource } from '@/lib/types'

export const asDate = (s: string | null) => dateIN(s ? s.replace(' ', 'T') : null)

/** Marks the query's words (three letters or more) inside a passage. */
function highlight(text: string, q: string): ReactNode {
  const words = q.toLowerCase().split(/\W+/).filter((w) => w.length >= 3)
  if (!words.length) return text
  const re = new RegExp(`(${words.join('|')})`, 'gi')
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i} className="rounded-sm bg-brand-tint text-brand-deep">{part}</mark> : part))
}

export function KnowledgeTab() {
  const { data: src, error } = useApi<{ sources: KbSource[]; note: string }>('/policy/sources')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<{ q: string; results: KbHit[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function search(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      const r = await api<{ query: string; results: KbHit[] }>(`/policy/search?q=${encodeURIComponent(q.trim())}&k=8`)
      setHits({ q: r.query, results: r.results })
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : 'Search failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <section className="card space-y-3">
        <h2 className="section-head">Search the policy passages</h2>
        <form className="flex gap-2" onSubmit={search}>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="For example: maximum loan amount, penal charges, Key Facts Statement" />
          <Button type="submit" disabled={q.trim().length < 3 || busy}>{busy ? <Loader2 className="animate-spin" /> : <Search />} Search</Button>
        </form>
        {problem && <Banner tone="problem">{problem}</Banner>}
        {hits && hits.results.length === 0 && <p className="text-sm text-muted-foreground">No passage matches closely enough.</p>}
        {hits && hits.results.length > 0 && (
          <ul className="space-y-2">
            {hits.results.map((h) => (
              <li key={h.chunk_id} className="space-y-1 rounded-lg border border-line bg-white p-3">
                <a href={h.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-medium text-brand hover:underline">
                  {h.publisher} · {h.title}{h.section && ` · ${h.section}`} <ExternalLink className="size-3" />
                </a>
                <p className="text-xs leading-relaxed [overflow-wrap:anywhere] text-ink-2">{highlight(h.quote, hits.q)}</p>
                <p className="text-[0.62rem] text-muted-foreground">score {h.score} · version {h.version} · fetched {asDate(h.fetched_at)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="section-head">Sources</h2>
        {error ? <Banner tone="problem">{error.message}</Banner> : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>Publisher</th><th>Title</th><th>Version</th><th>Fetched</th><th>Read as</th><th>Passages</th></tr></thead>
              <tbody>
                {(src?.sources ?? []).map((s) => (
                  <tr key={s.source_id}>
                    <td>{s.publisher}</td>
                    <td className="whitespace-normal!"><a className="text-brand hover:underline" href={s.url} target="_blank" rel="noreferrer">{s.title}</a></td>
                    <td>{s.version}</td>
                    <td>{asDate(s.fetched_at)}</td>
                    <td className="font-mono text-[0.65rem]">{s.extraction_method}</td>
                    <td className="tabular">{s.chunks}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {src && <p className="text-xs text-muted-foreground">{src.note}</p>}
      </section>
    </div>
  )
}
