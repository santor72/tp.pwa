import { describe, expect, it } from 'vitest'

import { storedBasemap, yandexCoordinates } from '../src/YandexMapCanvas'

describe('подложка Яндекс Карт', () => {
  it('восстанавливает гибрид и выбирает схему для повреждённого значения', () => {
    expect(storedBasemap({ getItem: () => 'hybrid' })).toBe('hybrid')
    expect(storedBasemap({ getItem: () => 'satellite' })).toBe('map')
  })

  it('сохраняет работоспособность при недоступном localStorage', () => {
    expect(storedBasemap({ getItem: () => { throw new Error('blocked') } })).toBe('map')
  })

  it('явно преобразует GeoJSON longitude-latitude в порядок Яндекс Карт', () => {
    expect(yandexCoordinates({ type: 'Point', coordinates: [37.2, 55.1] })).toEqual([55.1, 37.2])
    expect(yandexCoordinates({ type: 'LineString', coordinates: [[37.2, 55.1], [37.3, 55.2]] })).toEqual([[55.1, 37.2], [55.2, 37.3]])
  })
})
