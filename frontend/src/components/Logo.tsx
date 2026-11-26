import { cn } from '@/lib/utils'

// An approval seal: a rupee sign inside a stamp ring. A neutral mark of our own: no Bank of Baroda logo or brand asset.
// `animated`: the inner ring becomes a slowly turning dashed seal (sign-in page only).
export function Mark({ className, animated = false }: { className?: string; animated?: boolean }) {
  return (
    <span className={cn('flex size-9 shrink-0 items-center justify-center rounded-[10px] bg-brand text-white', className)}>
      <svg viewBox="0 0 48 48" className="size-[78%]" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="24" cy="24" r="20" />
        {animated
          ? <circle className="seal-ring" cx="24" cy="24" r="15" strokeWidth="1.6" strokeDasharray="2.2 2.6" opacity=".55" />
          : <circle cx="24" cy="24" r="15" strokeWidth="1.4" opacity=".45" />}
        <path d="M17.5 16.5h13M17.5 21.5h13M17.5 16.5h3.8c3.4 0 5.2 2 5.2 5s-1.8 5-5.2 5h-3.8l8.5 8" />
      </svg>
    </span>
  )
}

export function Logo({ compact = false }: { compact?: boolean }) {
  if (compact) return <Mark />
  return (
    <span className="flex items-center gap-2.5">
      <Mark />
      <span className="flex flex-col leading-tight">
        <span className="text-[0.95rem] font-semibold tracking-tight text-ink">Baroda Lending Desk</span>
        <span className="mt-0.5 w-fit rounded-full bg-brand-tint px-1.5 py-px font-mono text-[0.56rem] font-medium uppercase tracking-[0.12em] text-brand-deep">
          Demo
        </span>
      </span>
    </span>
  )
}
