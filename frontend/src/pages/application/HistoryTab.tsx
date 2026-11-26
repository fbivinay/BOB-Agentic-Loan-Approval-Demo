import { Loader2 } from 'lucide-react'
import { Banner } from '@/components/Labels'
import { Timeline } from '@/components/Timeline'
import { useApi } from '@/lib/useApi'
import { ROLE_LABEL, type Role } from '@/lib/session'
import { statusLabel, tone } from '@/lib/status'
import type { ReviewView } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'

const EVENT: Record<string, string> = {
  APPLICATION_TAKEOVER: 'Taken over', TERM_CHANGE: 'Terms adjusted', DECISION_RECORDED: 'Decision recorded', DECISION_OVERRIDE: 'Decision overrode the AI',
}

/** The audit trail of the application's journey: who moved it, when and why. */
export function HistoryTab({ app }: TabProps) {
  const { data: r, error } = useApi<ReviewView>(`/applications/${app.id}/review?v=${app.version}`)
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!r) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading…</div>
  return (
    <section className="card space-y-3">
      <h2 className="section-head">History</h2>
      <p className="text-xs text-muted-foreground">From the append-only audit trail. Nothing here can be edited or removed.</p>
      <Timeline items={r.history.map((h) => ({
        at: h.at,
        title: h.event === 'STATUS_TRANSITION' ? `${h.from ? statusLabel(h.from) : 'Created'} → ${statusLabel(h.to ?? '')}` : EVENT[h.event] ?? statusLabel(h.event),
        by: h.actor_type === 'SYSTEM' ? 'System' : h.by ? `${h.by}${h.role ? ` (${ROLE_LABEL[h.role as Role] ?? h.role})` : ''}` : null,
        note: h.reason,
        tone: h.event === 'STATUS_TRANSITION' ? tone(h.to) : h.event === 'DECISION_OVERRIDE' ? 'watch' : 'neutral',
      }))} />
    </section>
  )
}
