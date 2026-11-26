import { useState, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

// Both dialogs close only when `onConfirm` resolves to something other than undefined; useAction resolves to undefined
// on a refused request, so the person keeps what they typed and can try again.

/** An action that needs a written reason of at least `min` characters (the same minimum the API enforces). */
export function ReasonDialog({ trigger, title, description, label, min, max = 1000, confirmLabel, initial = '', extra, destructive, onConfirm }: {
  trigger: ReactNode
  title: string
  description?: ReactNode
  label: string
  min: number
  max?: number
  confirmLabel: string
  initial?: string
  extra?: ReactNode
  destructive?: boolean
  onConfirm: (reason: string) => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState(initial)
  const [busy, setBusy] = useState(false)
  const n = reason.trim().length

  async function confirm() {
    setBusy(true)
    const r = await onConfirm(reason.trim())
    setBusy(false)
    if (r !== undefined) {
      setOpen(false)
      setReason(initial)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && setOpen(o)}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {extra}
        <div className="space-y-1.5">
          <Label htmlFor="reason">{label}</Label>
          <Textarea id="reason" rows={4} value={reason} maxLength={max} onChange={(e) => setReason(e.target.value)} disabled={busy} />
          <p className={n < min ? 'text-xs text-muted-foreground' : 'text-xs text-good'}>
            {n < min ? `At least ${min} characters (${n} so far). Recorded in the audit trail.` : 'Recorded in the audit trail.'}
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
          <Button variant={destructive ? 'destructive' : 'default'} onClick={confirm} disabled={busy || n < min}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** An action that only needs a deliberate second click, with what it will do spelled out. */
export function ConfirmDialog({ trigger, title, body, confirmLabel, destructive, onConfirm }: {
  trigger: ReactNode
  title: string
  body: ReactNode
  confirmLabel: string
  destructive?: boolean
  onConfirm: () => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  async function confirm() {
    setBusy(true)
    const r = await onConfirm()
    setBusy(false)
    if (r !== undefined) setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && setOpen(o)}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 text-sm text-ink-2">{body}</div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
          <Button variant={destructive ? 'destructive' : 'default'} onClick={confirm} disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
