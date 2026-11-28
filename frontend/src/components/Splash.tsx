import { Mark } from '@/components/Logo'

// The loading cover. Entirely CSS (see .splash in index.css): it starts at first paint and lifts on its
// own after 2.5 s, so it can't get stuck waiting on React. index.html ends it on any click or key.
export function Splash() {
  return (
    <div className="splash" aria-hidden="true">
      <div className="splash__inner">
        <Mark className="size-12 rounded-xl" />
        <div className="splash__name">Baroda Lending Desk</div>
        <div className="splash__sub">Demo · synthetic data</div>
        <div className="splash__tag">Enquiry · Documents · Assessment · Decision</div>
        <div className="splash__track">
          <span className="splash__fill" />
        </div>
        <div className="splash__pct" />
      </div>
    </div>
  )
}
