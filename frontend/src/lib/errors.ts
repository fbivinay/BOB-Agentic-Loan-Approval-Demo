// One sentence for the person, from an HTTP status and the API's `detail`. No imports, so tests load it directly.

type Issue = { loc?: (string | number)[]; msg?: string }

export function errorMessage(status: number, detail?: unknown): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length)
    return (detail as Issue[]).map((d) => `${d.loc?.length ? `${d.loc[d.loc.length - 1]}: ` : ''}${d.msg ?? 'invalid'}`).join('; ')
  if (status === 0) return 'Cannot reach the server. It may be waking up (free tier, up to a minute); try again shortly.'
  if (status === 401) return 'Your session has ended. Sign in again.'
  if (status === 403) return 'Your role does not allow this.'
  if (status === 404) return 'Not found.'
  if (status >= 500) return 'Something went wrong on the server. Try again.'
  return 'The request was refused.'
}
