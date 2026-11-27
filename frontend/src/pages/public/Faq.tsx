import { PublicLayout } from '@/pages/public/PublicLayout'

const QA: [string, string][] = [
  ['Does the AI approve loans?',
    'No. The AI reads documents, checks them, finds the relevant policy passages and recommends one of three outcomes. Only an Assistant Manager or a Branch Manager can approve or decline, and the database refuses any decision that does not come from them. Every recommendation is shown as "AI recommendation, human decides".'],
  ['Is any of the data real?',
    'No. Customers, accounts, bureau records, documents and history are synthetic, generated for this demo. PANs follow a synthetic pattern (SYNP…) that cannot be a real PAN. Text sent to Gemini has its PANs replaced first; a scanned image is read by Gemini as it is, which is why only synthetic documents may be uploaded.'],
  ['What does "Prepared demo data — not OCR" mean?',
    'Some demo documents are deliberately degraded scans. Their values are prepared in advance and labelled exactly like that. It is never treated as evidence: a person confirms or keys every value.'],
  ['Is there an OCR engine?',
    'No. Born-digital PDFs are read from their own text layer. Scans and photos are read by Gemini from the image, labelled "Read by Gemini (AI, not OCR)", and every value is an AI suggestion until a person verifies it.'],
  ['Why is disbursement simulated?',
    'A demo must never move money. The release follows the real steps (customer acceptance, a pre-disbursement checklist and a maker-checker release by a different person), but it records a simulated reference and credits no account.'],
  ['Where do the policy figures come from?',
    'From a versioned rule set. Each figure is labelled either as a demo assumption or as verified, with a quote from the official Bank of Baroda or RBI page. A daily monitor compares the official pages with the rules and proposes reviews.'],
  ['Who can change a rule?',
    'Only a Branch Manager, as a configurable demo permission. They draft a new version from the monitor\'s proposals, the golden test cases run, and they activate it with a reason. The monitor itself can never change a rule. Applications already in progress keep the version frozen at their intake.'],
  ['Why might the first page load be slow?',
    'The backend runs on a free server that sleeps after fifteen idle minutes. The sign-in page wakes it, which can take up to a minute.'],
  ['Where do the emails go?',
    'Every approval and disbursement email is redirected to a test inbox. The intended address is named in the message, and synthetic customers never receive anything.'],
  ['Can I try it?',
    'Yes. The sign-in page has one-click demo logins for the three roles. Anyone can use them, so what you see may include other visitors\' changes; the owner can reset the demo data.'],
]

export function Faq() {
  return (
    <PublicLayout eyebrow="FAQ" title="Questions people ask" lede="About the AI, the data and how the demo behaves.">
      <div className="max-w-3xl space-y-3">
        {QA.map(([q, a]) => (
          <details key={q} className="card group">
            <summary className="cursor-pointer list-none text-sm font-medium marker:hidden">
              <span className="mr-2 inline-block text-brand transition-transform group-open:rotate-90">›</span>{q}
            </summary>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">{a}</p>
          </details>
        ))}
      </div>
    </PublicLayout>
  )
}
