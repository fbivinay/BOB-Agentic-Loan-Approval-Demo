import { CircleDot, ExternalLink, FolderGit2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PublicLayout, Section } from '@/pages/public/PublicLayout'
import { REPO } from '@/pages/public/About'

export function Contact() {
  return (
    <PublicLayout eyebrow="Contact" title="Get in touch"
      lede="Questions, feedback or a problem with the demo: the repository is the place to raise it.">
      <Section title="On GitHub">
        <div className="card flex max-w-3xl flex-wrap items-center gap-3">
          <Button asChild><a href={REPO} target="_blank" rel="noreferrer"><FolderGit2 /> Repository <ExternalLink /></a></Button>
          <Button asChild variant="outline"><a href={`${REPO}/issues/new`} target="_blank" rel="noreferrer"><CircleDot /> Open an issue <ExternalLink /></a></Button>
        </div>
        <p className="max-w-3xl text-xs text-muted-foreground">
          This demo is not a Bank of Baroda service. For anything about a real loan or account, contact Bank of Baroda directly through its official channels.
        </p>
      </Section>
    </PublicLayout>
  )
}
