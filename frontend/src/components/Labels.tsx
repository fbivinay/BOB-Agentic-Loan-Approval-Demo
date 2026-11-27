import type { ReactNode } from 'react'
import { AlertTriangle, Bot, Info } from 'lucide-react'
import { Pill, StatusPill } from '@/components/StatusPill'
import { METHOD_LABEL, type Tone } from '@/lib/status'
import { cn } from '@/lib/utils'

/** The AI's recommendation, always with the words that put it in its place. */
export function AiPill({ recommendation }: { recommendation: string | null | undefined }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" title="AI recommendation, human decides">
      <Bot className="size-3.5 text-ink-3" aria-hidden="true" />
      {recommendation ? <StatusPill status={recommendation} /> : <Pill>Not assessed yet</Pill>}
    </span>
  )
}

/** How a value was read. Prepared demo text is never presented as OCR. */
export function MethodChip({ method }: { method: string | null | undefined }) {
  if (!method) return null
  return (
    <span className={cn('inline-flex items-center rounded-md border px-1.5 py-px font-mono text-[0.62rem]',
      method === 'PREPARED_DEMO_TEXT' ? 'border-watch/40 bg-watch-bg text-watch' : 'border-border bg-surface text-ink-2')}>
      {METHOD_LABEL[method] ?? method}
    </span>
  )
}

/** A full-width notice. Text the API supplies as a label goes in verbatim. */
export function Banner({ tone = 'neutral', children, className }: { tone?: Tone | 'brand'; children: ReactNode; className?: string }) {
  const styles = {
    brand: 'border-brand/30 bg-brand-tint text-brand-deep', good: 'border-good/30 bg-good-bg text-good', watch: 'border-watch/30 bg-watch-bg text-watch',
    problem: 'border-problem/30 bg-problem-bg text-problem', neutral: 'border-border bg-surface/70 text-ink-2',
  }[tone]
  const Icon = tone === 'problem' || tone === 'watch' ? AlertTriangle : Info
  return (
    <div role="note" className={cn('flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-xs leading-relaxed', styles, className)}>
      <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      <div>{children}</div>
    </div>
  )
}
