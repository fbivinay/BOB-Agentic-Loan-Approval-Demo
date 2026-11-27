import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { BarChart3 } from 'lucide-react'
import { PageHead } from '@/components/PageHead'
import { PageSkeleton } from '@/components/AppShell'
import { Figure, FiguresPanel } from '@/components/Figure'
import { EmptyState } from '@/components/EmptyState'
import { StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { dateTimeIN, inr, pct } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import { monthlySeries, type MonthlyRow } from '@/lib/analytics'

type Overview = {
  headline: {
    historical: { decisions: number; approval_rate: number | null; avg_tat_days: number | null; approved_amount: number }
    live: { decisions: number; approval_rate: number | null; override_rate: number | null; disbursed_net: number }
    portfolio: { loans: number; outstanding: number }
  }
  monthly: MonthlyRow[]
  by_channel: { source: string; channel: string; decisions: number; approved: number; approval_rate: number; avg_tat_days: number | null }[]
  ai_vs_human: { ai_recommendation: string; outcome: string; is_override: boolean; decisions: number }[]
  funnel: { step: number; status: string; applications: number }[]
  portfolio: { product_type: string; status: string; loans: number; sanctioned: number; outstanding: number; weighted_rate: number }[]
  dpd: { bucket: string; bucket_order: number; loans: number; outstanding: number }[]
  as_of: string
  labels: { historical: string; live: string; data: string }
}

const BRAND = '#C2410C'
const SOFT = '#FED7AA'
const GOOD = '#16704a'
const LINE = '#e3e3e6'
const tip = { borderRadius: 12, border: `1px solid ${LINE}`, fontSize: 12 }

function Panel({ title, source, children }: { title: string; source: string; children: React.ReactNode }) {
  return (
    <section className="card space-y-3">
      <h2 className="section-head">{title}</h2>
      {children}
      <p className="text-[0.65rem] text-muted-foreground">{source}</p>
    </section>
  )
}

export function Analytics() {
  return (
    <Need any={['analytics.view']}>
      <Body />
    </Need>
  )
}

function Body() {
  const { data: a, error } = useApi<Overview>('/analytics/overview')
  if (error) return <div className="card"><EmptyState icon={BarChart3} line={error.message} /></div>
  if (!a) return <PageSkeleton />
  const h = a.headline
  const series = monthlySeries(a.monthly).map((m) => ({ ...m, label: new Date(`${m.month}-01T00:00:00Z`).toLocaleDateString('en-IN', { month: 'short', year: '2-digit', timeZone: 'UTC' }) }))
  const recs = [...new Set(a.ai_vs_human.map((r) => r.ai_recommendation))]
  const outcomes = [...new Set(a.ai_vs_human.map((r) => r.outcome))]
  const cell = (rec: string, out: string) => a.ai_vs_human.filter((r) => r.ai_recommendation === rec && r.outcome === out).reduce((s, r) => s + r.decisions, 0)
  const funnelMax = Math.max(1, ...a.funnel.map((f) => f.applications))

  return (
    <div className="space-y-6">
      <PageHead eyebrow="Lending performance" title="Analytics"
        lede={<>{a.labels.data} {a.labels.historical}. Live: {a.labels.live.toLowerCase()}. As of {dateTimeIN(a.as_of)}.</>} />

      <FiguresPanel cols={4}>
        <Figure label="Historical decisions" value={h.historical.decisions.toLocaleString('en-IN')} />
        <Figure label="Approval rate" value={pct(h.historical.approval_rate)} />
        <Figure label="Average turnaround" value={h.historical.avg_tat_days === null ? '—' : `${h.historical.avg_tat_days.toFixed(1)} days`} />
        <Figure label="Approved amount" value={inr(h.historical.approved_amount, { compact: true })} />
      </FiguresPanel>
      <FiguresPanel cols={4}>
        <Figure label="Live decisions" value={String(h.live.decisions)} />
        <Figure label="Live approval rate" value={pct(h.live.approval_rate)} />
        <Figure label="AI overridden" value={pct(h.live.override_rate)} tone={h.live.override_rate ? 'watch' : undefined} />
        <Figure label="Disbursed (simulated)" value={inr(h.live.disbursed_net, { compact: true })} />
      </FiguresPanel>

      <Panel title="Decisions month by month" source={`Decisions: ${a.labels.historical}; ${a.labels.live}. Line: approval rate.`}>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={series} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid vertical={false} stroke={LINE} />
              <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} interval="preserveStartEnd" />
              <YAxis yAxisId="n" tickLine={false} axisLine={false} fontSize={11} width={40} />
              <YAxis yAxisId="r" orientation="right" tickLine={false} axisLine={false} fontSize={11} width={44} domain={[0, 1]} tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`} />
              <Tooltip cursor={{ fill: 'rgba(11,11,12,0.05)' }} contentStyle={tip}
                formatter={(v, n) => (n === 'Approval rate' ? [pct(Number(v)), n] : [String(v), n])} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: '#4a4a52' }}>{v}</span>} />
              <Bar yAxisId="n" dataKey="historical" name="Historical" stackId="d" fill={SOFT} radius={[0, 0, 0, 0]} animationDuration={600} />
              <Bar yAxisId="n" dataKey="live" name="Live (demo)" stackId="d" fill={BRAND} radius={[6, 6, 0, 0]} animationDuration={600} />
              <Line yAxisId="r" dataKey="approval_rate" name="Approval rate" type="monotone" stroke={GOOD} strokeWidth={2} dot={false} animationDuration={600} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <div className="grid grid-cols-2 gap-4">
        <Panel title="By channel" source={a.labels.historical}>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>Source</th><th>Channel</th><th>Decisions</th><th>Approval rate</th><th>Turnaround</th></tr></thead>
              <tbody>
                {a.by_channel.map((c) => (
                  <tr key={`${c.source}-${c.channel}`}>
                    <td>{statusLabel(c.source)}</td><td>{c.channel}</td><td className="tabular">{c.decisions}</td><td className="tabular">{pct(c.approval_rate)}</td>
                    <td className="tabular">{c.avg_tat_days === null ? '—' : `${c.avg_tat_days.toFixed(1)} d`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="AI recommendation against the human decision" source={a.labels.live}>
          {recs.length === 0 ? <p className="text-sm text-muted-foreground">No live decisions yet.</p> : (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead><tr><th>AI recommended</th>{outcomes.map((o) => <th key={o}>{statusLabel(o)}</th>)}</tr></thead>
                <tbody>
                  {recs.map((r) => (
                    <tr key={r}><td><StatusPill status={r} /></td>{outcomes.map((o) => <td key={o} className="tabular">{cell(r, o) || '—'}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Panel title="Where live applications are" source={a.labels.live}>
          <ul className="space-y-1.5">
            {a.funnel.map((f) => (
              <li key={`${f.step}-${f.status}`} className="grid grid-cols-[10rem_1fr_2rem] items-center gap-2 text-xs">
                <span>{statusLabel(f.status)}</span>
                <span className="h-2 rounded-full bg-line"><span className="block h-2 rounded-full bg-brand" style={{ width: `${(f.applications / funnelMax) * 100}%` }} /></span>
                <span className="tabular text-right">{f.applications}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Existing loan book" source={`${a.labels.data} ${h.portfolio.loans} loans, ${inr(h.portfolio.outstanding, { compact: true })} outstanding.`}>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>Days past due</th><th>Loans</th><th>Outstanding</th></tr></thead>
              <tbody>
                {a.dpd.map((d) => <tr key={d.bucket}><td>{d.bucket}</td><td className="tabular">{d.loans}</td><td className="tabular">{inr(d.outstanding, { compact: true })}</td></tr>)}
              </tbody>
            </table>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>Product</th><th>Status</th><th>Loans</th><th>Outstanding</th><th>Avg rate</th></tr></thead>
              <tbody>
                {a.portfolio.map((p) => (
                  <tr key={`${p.product_type}-${p.status}`}>
                    <td>{statusLabel(p.product_type)}</td><td><StatusPill status={p.status} /></td><td className="tabular">{p.loans}</td>
                    <td className="tabular">{inr(p.outstanding, { compact: true })}</td><td className="tabular">{p.weighted_rate ? `${p.weighted_rate.toFixed(2)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  )
}
