import { cn } from '@/lib/utils'

// A rupee sign on the brand tile. A neutral mark of our own: no Bank of Baroda logo or brand asset.
export function Mark({ className }: { className?: string }) {
  return (
    <span className={cn('flex size-9 shrink-0 items-center justify-center rounded-[10px] bg-brand text-white', className)}>
      <svg viewBox="0 0 24 24" className="size-[60%]" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M7 4.5h10M7 9h10M7 4.5c4 0 5.8 1.8 5.8 4.5S11 13.5 7 13.5l7.5 6" />
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
