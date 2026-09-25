import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, gisRequestZoom } from '../src/api'

afterEach(() => vi.unstubAllGlobals())

describe('Ошибки загрузки фотоотчёта', () => {
  const complete = () => api.completeConnection(54295, {
    day: 'today', idempotencyKey: 'test-key', techportalText: 'Работа выполнена',
    gisText: '', photos: [new File(['photo'], 'photo.jpg', { type: 'image/jpeg' })],
  }, 'csrf-test')

  it('объясняет превышение размера при HTML-ответе 413 от прокси', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      '<html><body>413 Request Entity Too Large</body></html>',
      { status: 413, headers: { 'Content-Type': 'text/html' } },
    )))

    await expect(complete()).rejects.toMatchObject({
      status: 413, code: 'REQUEST_TOO_LARGE',
      message: 'Размер вложений превышает допустимый для отправки. Уменьшите размер или количество фотографий.',
    })
  })

  it('сохраняет описание ограничения, если его вернул backend', async () => {
    const error = { code: 'REPORT_PHOTO_TOO_LARGE', message: 'Фотография превышает 10 МиБ' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(error), {
      status: 413, headers: { 'Content-Type': 'application/json' },
    })))

    await expect(complete()).rejects.toMatchObject({ status: 413, ...error })
  })
})

describe('масштаб запросов объектов GIS', () => {
  it('преобразует плавный масштаб в допустимое целое значение', () => {
    expect(gisRequestZoom(14.9)).toBe(14)
    expect(gisRequestZoom(0.5)).toBe(1)
    expect(gisRequestZoom(27)).toBe(23)
  })
})
