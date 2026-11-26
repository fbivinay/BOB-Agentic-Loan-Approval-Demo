import { Logo } from '@/components/Logo'
import { Splash } from '@/components/Splash'
import { Button } from '@/components/ui/button'

// Task 1 placeholder: shows the design tokens. Replaced by the real routes in Task 2.
export default function App() {
  return (
    <>
      <Splash />
      <main className="shell py-10">
        <div className="space-y-6">
          <Logo />
          <div className="card space-y-3">
            <p className="eyebrow">Design tokens</p>
            <h1 className="display">Baroda Lending Desk</h1>
            <div className="flex gap-2">
              <Button>Primary action</Button>
              <Button variant="outline">Secondary</Button>
            </div>
            <div className="flex gap-2">
              <span className="pill pill--good">Approved</span>
              <span className="pill pill--watch">Under review</span>
              <span className="pill pill--problem">Declined</span>
              <span className="pill pill--neutral">Draft</span>
            </div>
          </div>
        </div>
      </main>
    </>
  )
}
