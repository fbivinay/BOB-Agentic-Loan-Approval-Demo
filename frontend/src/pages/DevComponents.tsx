import { Inbox } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { StatusPill } from '@/components/StatusPill'
import { DataTable } from '@/components/DataTable'
import { EmptyState } from '@/components/EmptyState'
import { ConfirmDialog, ReasonDialog } from '@/components/Dialogs'
import { AiPill, Banner, MethodChip } from '@/components/Labels'
import { Timeline } from '@/components/Timeline'

// Temporary page for checking the shared components by eye; removed once the real pages use them all.
export function DevComponents() {
  return (
    <div className="space-y-6">
      <PageHead eyebrow="Check" title="Components" lede="Every shared component, once." actions={<Button>Action</Button>} />
      <FiguresPanel cols={3}>
        <Figure label="Open" value="101" note="29 in progress" />
        <Figure label="Late" value="55" tone="problem" onClick={() => {}} active />
        <Figure label="Amount" value="₹1.48 Cr" tone="good" />
      </FiguresPanel>
      <div className="flex flex-wrap gap-2">
        {['APPROVED', 'UNDER_REVIEW', 'DECLINED', 'DRAFT'].map((s) => <StatusPill key={s} status={s} />)}
      </div>
      <AiPill recommendation="REFER_FOR_FURTHER_REVIEW" />
      <div className="flex gap-2"><MethodChip method="PREPARED_DEMO_TEXT" /><MethodChip method="EMBEDDED_TEXT" /></div>
      <Banner tone="watch">SIMULATED DISBURSEMENT - no money moves and no real account is credited. Demo of the workflow only.</Banner>
      <div className="flex gap-2">
        <ReasonDialog trigger={<Button variant="outline">Reason dialog</Button>} title="Decline" label="Reason" min={10} confirmLabel="Decline"
          onConfirm={async () => 'ok'} />
        <ConfirmDialog trigger={<Button variant="outline">Confirm dialog</Button>} title="Release" body="This is a simulated release." confirmLabel="Release"
          onConfirm={async () => 'ok'} />
      </div>
      <DataTable rows={[{ a: 'APP-2026-000001', b: 'Kavya Sharma' }]} columns={[{ accessorKey: 'a', header: 'Reference' }, { accessorKey: 'b', header: 'Applicant' }]} />
      <DataTable rows={[]} columns={[{ accessorKey: 'a', header: 'Reference' }]} empty="No applications yet." />
      <div className="card"><EmptyState icon={Inbox} line="Nothing here." /></div>
      <div className="card"><Timeline items={[{ at: '2026-11-26T09:37:43Z', title: 'Intake → Documents pending', by: 'Meera Iyer', tone: 'neutral' },
        { at: '2026-11-26T10:37:43Z', title: 'Approved', by: 'Rohan Deshmukh', note: 'Within policy.', tone: 'good' }]} /></div>
    </div>
  )
}
