import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { isBusy, onBusyChange } from '@/lib/api'

// A thin bar across the top while the backend answers. It shoots ahead, then slows as if about to finish, and
// completes the moment the answer lands. Answers quicker than 150 ms never show it; back-to-back requests share one bar.
export function TopProgress() {
  const busy = useSyncExternalStore(onBusyChange, isBusy)
  const [on, setOn] = useState(false)
  const bar = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (busy) return setOn(true)
    const id = window.setTimeout(() => setOn(false), 120)
    return () => window.clearTimeout(id)
  }, [busy])

  useEffect(() => {
    const el = bar.current
    if (!on || !el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const grow = el.animate(
      [{ transform: 'scaleX(0)' }, { transform: 'scaleX(0.94)' }],
      { duration: 10_000, delay: 150, easing: 'cubic-bezier(0.05, 0.75, 0.1, 1)', fill: 'both' },
    )
    return () => {
      const shown = Number(grow.currentTime ?? 0) > 170
      const from = getComputedStyle(el).transform
      grow.cancel()
      if (shown) el.animate(
        [{ transform: from, opacity: 1 }, { transform: 'scaleX(1)', opacity: 1, offset: 0.5 }, { transform: 'scaleX(1)', opacity: 0 }],
        { duration: 450, easing: 'ease-out' },
      )
    }
  }, [on])

  return <div ref={bar} className="top-progress" aria-hidden="true" />
}
