import { useEffect, useId, useState } from 'react'
import { Skeleton } from '@/components/ui/skeleton'

// Renders a mermaid diagram. The library loads on first use only (the architecture page), in its own chunk.
// Neutral lines and surfaces; strict security, so labels cannot inject markup.
let ready: Promise<typeof import('mermaid').default> | null = null
function load() {
  ready ??= import('mermaid').then(({ default: m }) => {
    m.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'base',
      fontFamily: 'Geist Variable, ui-sans-serif, system-ui, sans-serif',
      themeVariables: {
        primaryColor: '#ffffff', primaryBorderColor: '#d2d2d7', primaryTextColor: '#0b0b0c', lineColor: '#6b6b74', secondaryColor: '#FFF7ED',
        tertiaryColor: '#fafafa', clusterBkg: '#FFF7ED', clusterBorder: '#FED7AA', actorBkg: '#ffffff', actorBorder: '#C2410C', noteBkgColor: '#FFF7ED',
        fontSize: '14px',
      },
    })
    return m
  })
  return ready
}

export function Mermaid({ chart }: { chart: string }) {
  const id = `m${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    load().then((m) => m.render(id, chart)).then((r) => live && setSvg(r.svg)).catch(() => live && setFailed(true))
    return () => { live = false }
  }, [chart, id])
  if (failed) return <pre className="overflow-auto rounded-lg bg-white p-3 text-xs">{chart}</pre>
  if (!svg) return <Skeleton className="h-72 w-full rounded-2xl" />
  return <div className="overflow-x-auto [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />
}
