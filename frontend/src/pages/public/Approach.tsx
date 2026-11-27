import { Bot, Database, FileSearch, Gavel, Layers, ScanSearch, ShieldCheck, Sparkles } from 'lucide-react'
import { Figure, FiguresPanel } from '@/components/Figure'
import { PublicLayout, Section } from '@/pages/public/PublicLayout'

const LAKEHOUSE = [
  { icon: Database, title: 'Raw', body: 'Synthetic bank, bureau and KYC records land unchanged in BigQuery (lh_raw): customers, accounts, transactions, loans and repayments.' },
  { icon: Layers, title: 'Curated', body: 'Cleaned and joined (lh_curated): salary regularity, obligations, days past due, policy passages from the official pages.' },
  { icon: Sparkles, title: 'Gold', body: 'What the agents and dashboards read (lh_gold): a customer 360 per applicant and the lending KPIs.' },
  { icon: ShieldCheck, title: 'Ground truth, isolated', body: 'The generator\'s answers sit in lh_truth, which the application cannot read. The AI is evaluated against it, never trained on it.' },
]
const AGENTS = [
  { icon: FileSearch, title: 'Document graph', body: 'Reads each upload by one labelled method, extracts fields that must quote their source, and checks them. A person confirms or corrects every value.' },
  { icon: ScanSearch, title: 'Assessment graph', body: 'Gathers the evidence, runs the deterministic policy engine, cites the official passages and writes an explanation that may not invent numbers.' },
  { icon: Bot, title: 'Policy monitor', body: 'Reads the RBI and Bank of Baroda pages every day and turns real changes into proposals. It never changes a rule by itself.' },
  { icon: Gavel, title: 'People decide', body: 'Decisions, approved terms, verification, rule activation and the release are human steps, enforced by the database and recorded in an append-only audit trail.' },
]

function Cards({ items }: { items: typeof LAKEHOUSE }) {
  return (
    <ol className="grid grid-cols-4 gap-4">
      {items.map(({ icon: Icon, title, body }, i) => (
        <li key={title} className="card space-y-2">
          <span className="flex items-center gap-2 text-sm font-medium">
            <span className="flex size-7 items-center justify-center rounded-lg bg-brand-tint text-brand"><Icon className="size-4" /></span>
            {i + 1}. {title}
          </span>
          <p className="text-sm leading-relaxed text-ink-2">{body}</p>
        </li>
      ))}
    </ol>
  )
}

export function Approach() {
  return (
    <PublicLayout eyebrow="Approach" title="Lakehouse first, then agents"
      lede="A branch personal loan passes through many hands. Most of the time goes on checking documents and assembling evidence: that is what AI can speed up. The decision stays with the people accountable for it, which is a regulatory expectation as well as a design choice.">
      <FiguresPanel cols={3}>
        <Figure label="Decided applications in the synthetic history" value="1,902" />
        <Figure label="Average turnaround" value="5.6 days" />
        <Figure label="Approval rate" value="70.9%" />
      </FiguresPanel>
      <Section title="1. Lakehouse first">
        <p className="max-w-3xl text-sm text-ink-2">Before any agent, the data: one place where every fact about an applicant has a source, a layer and an owner.</p>
        <Cards items={LAKEHOUSE} />
      </Section>
      <Section title="2. Then the agentic solution">
        <p className="max-w-3xl text-sm text-ink-2">Agents where judgement-free reading helps; plain code for eligibility, pricing, EMI and APR, with versioned and labelled parameters.</p>
        <Cards items={AGENTS} />
      </Section>
      <Section title="What the constraints taught">
        <ul className="grid max-w-4xl list-disc gap-2 pl-5 text-sm text-ink-2">
          <li><strong>Say exactly how each value was read.</strong> The demo uses a PDF's own text where it exists and otherwise has Gemini read the scan or photo, labelled as AI reading, not OCR. Every value is a suggestion until a person verifies it.</li>
          <li><strong>Model output is checked, never trusted.</strong> Extracted values must appear in their quotes; explanations may not invent numbers or decide.</li>
          <li><strong>Free tiers shape the design:</strong> load jobs instead of updates in BigQuery's sandbox, a scheduled drain for a sleeping server, synthetic text only for the AI, and a test inbox for every email.</li>
        </ul>
      </Section>
    </PublicLayout>
  )
}
