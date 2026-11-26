import { Mermaid } from '@/components/Mermaid'
import { PublicLayout, Section } from '@/pages/public/PublicLayout'
import { ASSESSMENT_GRAPH, DOCUMENT_GRAPH, LIFECYCLE, MONITOR, SYSTEM } from '@/pages/public/diagrams'

// As in docs/REQUIREMENTS.md (permissions matrix). Configurable ones are demo permissions.
const MATRIX: [string, boolean, boolean, boolean, boolean][] = [
  ['Enquiries: create, edit, convert, close', true, false, false, false],
  ['Intake, uploads, verify documents, resolve discrepancies, attest', true, false, false, false],
  ['Forward an assessed application', true, false, false, false],
  ['Claim, approve or decline', false, true, true, false],
  ['Take over, refer for reconsideration, adjust terms', false, false, true, false],
  ['Record acceptance and the pre-disbursement checklist', true, false, false, true],
  ['Release a simulated disbursement', false, true, true, true],
  ['Review reports, draft and activate rule sets', false, false, true, true],
  ['Analytics and system status', false, true, true, false],
]

function Diagram({ title, chart, caption }: { title: string; chart: string; caption: string }) {
  return (
    <Section title={title}>
      <div className="card space-y-3">
        <Mermaid chart={chart} />
        <p className="text-sm text-ink-2">{caption}</p>
      </div>
    </Section>
  )
}

export function Architecture() {
  return (
    <PublicLayout eyebrow="Architecture" title="How it fits together"
      lede="The AI extracts, checks, retrieves policy and recommends. People decide, and the database enforces that. Free tiers and synthetic data only.">
      <Diagram title="System context" chart={SYSTEM}
        caption="The backend is the only client of the database, storage, BigQuery, Gemini and Resend. The browser holds only the Supabase anon key and the signed-in person's token; roles are read from the database on every request." />
      <Diagram title="A loan, start to finish" chart={LIFECYCLE}
        caption="Every status change is a database transition with its actor, and the append-only audit trail records who did what and why." />
      <Diagram title="Document graph" chart={DOCUMENT_GRAPH}
        caption="One labelled text method per document. Prepared demo text is never evidence, and every AI value is a suggestion until a person confirms it." />
      <Diagram title="Assessment graph" chart={ASSESSMENT_GRAPH}
        caption="The recommendation comes from the deterministic engine alone. A missing credit history is never a failure: thin-file and new-to-credit applicants are assessed on the evidence that exists." />
      <Diagram title="Policy monitor" chart={MONITOR}
        caption="The monitor can only write reports and proposals. A rule changes only when a Branch Manager drafts a version, the golden cases pass and they activate it with a reason." />
      <Section title="Who may do what">
        <div className="data-table-wrap">
          <table className="data-table">
            <thead><tr><th>Action</th><th>Loan Officer</th><th>Assistant Manager</th><th>Branch Manager</th><th>Kind</th></tr></thead>
            <tbody>
              {MATRIX.map(([what, lo, am, bm, demo]) => (
                <tr key={what}>
                  <td className="whitespace-normal!">{what}</td>
                  {[lo, am, bm].map((v, i) => <td key={i} className="text-center">{v ? '✔' : ''}</td>)}
                  <td className="text-xs">{demo ? 'Demo permission' : 'Fixed'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-muted-foreground">Demo permissions are configurable and are not Bank of Baroda's actual authorization structure. The Loan Officer can never approve or decline.</p>
      </Section>
    </PublicLayout>
  )
}
