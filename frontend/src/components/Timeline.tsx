import { dateTimeIN } from '@/lib/format'
import type { Tone } from '@/lib/status'
import { cn } from '@/lib/utils'

export type TimelineItem = { at: string; title: string; by?: string | null; note?: string | null; tone?: Tone }

const DOT: Record<Tone, string> = { good: 'bg-good', watch: 'bg-watch', problem: 'bg-problem', neutral: 'bg-ink-3' }

/** Oldest first: what happened, who did it, why, and when. */
export function Timeline({ items }: { items: TimelineItem[] }) {
  if (!items.length) return <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
  return (
    <ol className="relative space-y-4 border-l border-line pl-5">
      {items.map((it, i) => (
        <li key={i} className="relative">
          <span className={cn('absolute top-1.5 -left-[1.62rem] size-2.5 rounded-full ring-4 ring-ground', DOT[it.tone ?? 'neutral'])} />
          <p className="text-sm font-medium">{it.title}</p>
          <p className="text-xs text-muted-foreground">
            {dateTimeIN(it.at)}
            {it.by && <> · {it.by}</>}
          </p>
          {it.note && <p className="mt-1 text-xs leading-relaxed text-ink-2">{it.note}</p>}
        </li>
      ))}
    </ol>
  )
}
