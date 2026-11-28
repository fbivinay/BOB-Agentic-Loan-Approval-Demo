import type { ReactNode } from 'react'
import { CountUp } from '@/components/CountUp'
import { Skeleton } from '@/components/ui/skeleton'
import type { Tone } from '@/lib/status'
import { cn } from '@/lib/utils'

// The brand-ringed panel that holds a page's opening figures; `cols` is how many sit in a row.
export function FiguresPanel({ children, cols = 3 }: { children: ReactNode; cols?: 2 | 3 | 4 | 5 }) {
  const grid = { 2: 'grid-cols-2', 3: 'grid-cols-3', 4: 'grid-cols-4', 5: 'grid-cols-5' }[cols]
  return (
    <section className="figures-panel">
      <div className={cn('grid gap-4', grid)}>{children}</div>
    </section>
  )
}

// One box of a figures panel: a big number, a label and a short note. With `onClick` the box is a button (a list's
// filter); `active` marks the box whose filter is applied. A text value counts up the first time it is seen.
// An undefined value is still loading: the box shimmers where the number will be.
export function Figure({ label, value, note, tone, onClick, active }: {
  label: string
  value: ReactNode
  note?: ReactNode
  tone?: Tone
  onClick?: () => void
  active?: boolean
}) {
  const loading = value === undefined
  const long = typeof value === 'string' && value.length > 10
  const body = (
    <>
      <p className="figure__label">{label}</p>
      {loading ? (
        <div className="figure__value"><Skeleton className="mx-auto h-[1.1em] w-24 rounded-lg" /></div>
      ) : (
        <div className={cn('figure__value', long && 'figure__value--long')}>
          {typeof value === 'string' || typeof value === 'number' ? <CountUp text={String(value)} /> : value}
        </div>
      )}
      {note && !loading && <p className="figure__note">{note}</p>}
    </>
  )
  const cls = cn('figure', tone && tone !== 'neutral' && `figure--${tone}`)
  if (!onClick) return <div className={cls}>{body}</div>
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(cls, 'w-full cursor-pointer rounded-xl transition hover:ring-2 hover:ring-brand/30 focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none',
        active && 'ring-2 ring-brand')}
    >
      {body}
    </button>
  )
}
