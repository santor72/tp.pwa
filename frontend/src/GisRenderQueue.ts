/** Keep map updates below one animation-frame budget while allowing cancellation. */
export const GIS_RENDER_SLICE_MS = 6

function now() { return globalThis.performance?.now?.() ?? Date.now() }

function yieldToBrowser(): Promise<void> {
  const scheduler = (globalThis as any).scheduler
  if (typeof scheduler?.yield === 'function') return scheduler.yield()
  return new Promise(resolve => window.setTimeout(resolve, 0))
}

export async function renderInBatches<T>(items: Iterable<T>, render: (item: T) => void, signal: AbortSignal, sliceMs = GIS_RENDER_SLICE_MS): Promise<boolean> {
  let startedAt = now()
  for (const item of items) {
    if (signal.aborted) return false
    render(item)
    if (now() - startedAt < sliceMs) continue
    await yieldToBrowser()
    if (signal.aborted) return false
    startedAt = now()
  }
  return !signal.aborted
}
