import { describe, expect, it } from 'vitest'

import { renderInBatches } from '../src/GisRenderQueue'

describe('порционная отрисовка GIS', () => {
  it('останавливается после отмены между порциями', async () => {
    const controller = new AbortController(); const rendered: number[] = []
    const original = globalThis.scheduler
    Object.defineProperty(globalThis, 'scheduler', { configurable: true, value: { yield: async () => controller.abort() } })
    try {
      await expect(renderInBatches([1, 2, 3], item => rendered.push(item), controller.signal, 0)).resolves.toBe(false)
      expect(rendered).toEqual([1])
    } finally {
      Object.defineProperty(globalThis, 'scheduler', { configurable: true, value: original })
    }
  })
})
