/** Last answer per GET path, so a screen opened again shows at once while useApi re-reads it.
 *  Belongs to one signed-in user: session.tsx clears it whenever that user changes. */
const answers = new Map<string, unknown>()
// Bumped on every clear, so an answer to a request started before the clear (another user's session) is never stored.
let generation = 0

export const cached = <T,>(path: string) => answers.get(path) as T | undefined
export const cacheGeneration = () => generation
/** Stores `data` only if the cache was not cleared since `startedAt` (from cacheGeneration() when the request began). */
export function remember(path: string, data: unknown, startedAt: number) {
  if (startedAt === generation) answers.set(path, data)
}
export function clearApiCache() {
  generation++
  answers.clear()
}
