import { Link } from 'react-router-dom'

export function NotFound() {
  return (
    <div className="flex flex-col items-center gap-3 py-24 text-center">
      <p className="text-base font-medium">There is no page here.</p>
      <Link to="/overview" className="text-sm text-brand underline-offset-4 hover:underline">Go to the overview</Link>
    </div>
  )
}
