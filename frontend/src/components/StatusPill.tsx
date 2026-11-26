import type { ReactNode } from 'react'
import { statusLabel, tone, type Tone } from '@/lib/status'
import { cn } from '@/lib/utils'

export function Pill({ children, tone = 'neutral', className }: { children: ReactNode; tone?: Tone | 'ink'; className?: string }) {
  return <span className={cn('pill', `pill--${tone}`, className)}>{children}</span>
}

// Colour only ever means status (docs/PLAN.md 14A).
export function StatusPill({ status, className }: { status: string | null | undefined; className?: string }) {
  if (!status) return null
  return <Pill tone={tone(status)} className={className}>{statusLabel(status)}</Pill>
}
