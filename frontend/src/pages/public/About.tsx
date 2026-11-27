import { Fragment } from 'react'
import { ExternalLink, FolderGit2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PublicLayout, Section } from '@/pages/public/PublicLayout'

const REPO = 'https://github.com/fbivinay/BOB-Agentic-Loan-Approval-Demo'

const STACK = [
  ['Staff app', 'Vite, React and TypeScript on Vercel Hobby'],
  ['Backend', 'FastAPI on Render (free tier), LangGraph for the agent workflows'],
  ['Workflow and audit', 'Supabase: Auth, Postgres with database-enforced guards, private Storage'],
  ['Lakehouse', 'BigQuery sandbox: raw, curated and gold layers, ground truth isolated'],
  ['AI', 'Gemini Developer API for reading scans, extraction, policy facts and explanations'],
  ['Email', 'Resend, every message redirected to a test inbox'],
  ['Scheduling', 'GitHub Actions: hourly job drain, daily policy monitor'],
]

export function About() {
  return (
    <PublicLayout eyebrow="About" title="About this demo"
      lede="Baroda Lending Desk (Demo) shows how agentic AI could support personal-loan processing at a bank branch, built for the Bank of Baroda business analyst assignment.">
      <Section title="What it is">
        <div className="card max-w-3xl space-y-2 text-sm leading-relaxed text-ink-2">
          <p>A working demo of the standard personal loan, from the first enquiry to a simulated disbursement, with three branch roles: Loan Officer, Assistant Manager and Branch Manager.</p>
          <p>All data is synthetic. The demo is not affiliated with Bank of Baroda, uses no official logo or brand asset, issues no real loan and moves no money. Policy figures are labelled either as demo assumptions or as verified against a cited official page.</p>
          <p>It runs entirely on free tiers, at zero cost.</p>
        </div>
      </Section>
      <Section title="How it is built">
        <dl className="card grid max-w-3xl grid-cols-[11rem_1fr] gap-y-2 text-sm">
          {STACK.map(([k, v]) => <Fragment key={k}><dt className="text-muted-foreground">{k}</dt><dd>{v}</dd></Fragment>)}
        </dl>
      </Section>
      <Section title="Source code">
        <Button asChild variant="outline"><a href={REPO} target="_blank" rel="noreferrer"><FolderGit2 /> The repository on GitHub <ExternalLink /></a></Button>
      </Section>
    </PublicLayout>
  )
}

export { REPO }
